"""Adaptateur MetaTrader 5 (paquet Python officiel ``MetaTrader5``, Windows uniquement).

Pièges gérés explicitement :
1. HEURE SERVEUR : MT5 renvoie les horodatages des bougies dans le fuseau du
   serveur du broker (souvent UTC+2/UTC+3, avec DST) présentés comme de l'UTC.
   On mesure le décalage à partir du dernier tick et on le retire.
2. IDEMPOTENCE : chaque ordre porte ``client_order_id`` dans son commentaire.
   Après un timeout / une perte de connexion, on vérifie si la position existe
   AVANT de renvoyer l'ordre -> jamais de doublon.
3. REQUOTES / PRIX CHANGÉ : nouvel essai avec un prix frais (``max_retries``).
4. MODE DE REMPLISSAGE : détecté depuis ``symbol_info().filling_mode``
   (FOK / IOC / RETURN selon le broker ; un mauvais choix = retcode 10030).
5. JAMAIS DE POSITION SANS STOP : si le SL n'est pas en place après exécution,
   on tente de le poser ; en cas d'échec, la position est fermée immédiatement.
6. Les commentaires MT5 sont limités (31 caractères) -> ids courts.
"""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

from tradebot.core.instrument import InstrumentSpec
from tradebot.core.logging import get_logger
from tradebot.core.models import AccountState, Bar, ExitReason, Fill, OrderIntent, Position, Side, Trade
from tradebot.execution.base import BrokerAdapter, OrderResult

log = get_logger(__name__)

# Retcodes MT5 (documentation MQL5 « Trade Server Return Codes »)
RC_REQUOTE = 10004
RC_PLACED = 10008
RC_DONE = 10009
RC_DONE_PARTIAL = 10010
RC_TIMEOUT = 10012
RC_INVALID_STOPS = 10016
RC_MARKET_CLOSED = 10018
RC_NO_MONEY = 10019
RC_PRICE_CHANGED = 10020
RC_PRICE_OFF = 10021
RC_INVALID_FILL = 10030
RC_CONNECTION = 10031
OK_CODES = {RC_DONE, RC_PLACED, RC_DONE_PARTIAL}
RETRY_PRICE = {RC_REQUOTE, RC_PRICE_CHANGED, RC_PRICE_OFF}
RETRY_UNKNOWN = {RC_TIMEOUT, RC_CONNECTION}


def import_mt5() -> ModuleType:
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover - dépend de la plateforme
        raise RuntimeError(
            "Paquet MetaTrader5 introuvable. Il n'existe que sous Windows : "
            "`uv sync --extra mt5` sur le VPS Windows."
        ) from e
    return mt5


class MT5Adapter(BrokerAdapter):
    name = "mt5"

    def __init__(self, symbol: str, magic: int, instrument: InstrumentSpec, *,
                 mt5: ModuleType | Any | None = None, deviation_points: int = 30,
                 max_retries: int = 3) -> None:
        self.mt5 = mt5 or import_mt5()
        self.symbol = symbol
        self.magic = magic
        self.inst = instrument
        self.deviation = deviation_points
        self.max_retries = max_retries
        self.server_offset = timedelta(0)
        self._known: dict[str, Position] = {}
        self._closed: list[Trade] = []

    # ------------------------------------------------------------ connexion
    def connect(self, login: int | None, password: str | None, server: str | None,
                path: str | None = None) -> None:
        kwargs: dict[str, Any] = {}
        if path:
            kwargs["path"] = path
        if login:
            kwargs.update(login=login, password=password, server=server)
        if not self.mt5.initialize(**kwargs):
            raise RuntimeError(f"initialisation MT5 impossible : {self.mt5.last_error()}")
        if not self.mt5.symbol_select(self.symbol, True):
            raise RuntimeError(f"symbole {self.symbol} indisponible chez ce broker")
        info = self.mt5.account_info()
        log.info("mt5_connected", server=getattr(info, "server", "?"),
                 trade_mode=getattr(info, "trade_mode", "?"), currency=getattr(info, "currency", "?"))
        self.detect_server_offset()

    def is_demo(self) -> bool:
        info = self.mt5.account_info()
        return getattr(info, "trade_mode", None) == getattr(self.mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)

    def detect_server_offset(self, now: datetime | None = None) -> timedelta:
        """Décalage heure serveur - UTC, arrondi à 30 min (dernier tick récent requis)."""
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return self.server_offset
        now = now or datetime.now(UTC)
        raw = datetime.fromtimestamp(tick.time, tz=UTC) - now
        half_hours = round(raw.total_seconds() / 1800)
        if abs(raw.total_seconds() - half_hours * 1800) < 600:  # tick récent (< 10 min)
            self.server_offset = timedelta(minutes=30 * half_hours)
        return self.server_offset

    def _to_utc(self, server_epoch: int | float) -> datetime:
        return datetime.fromtimestamp(server_epoch, tz=UTC) - self.server_offset

    def instrument_spec(self) -> InstrumentSpec:
        si = self.mt5.symbol_info(self.symbol)
        acc = self.mt5.account_info()
        return InstrumentSpec(
            symbol=self.symbol,
            contract_size=float(si.trade_contract_size),
            tick_size=float(si.trade_tick_size or si.point),
            volume_min=float(si.volume_min),
            volume_step=float(si.volume_step),
            volume_max=float(si.volume_max),
            leverage=float(getattr(acc, "leverage", self.inst.leverage) or self.inst.leverage),
        )

    # ------------------------------------------------------------ données
    def completed_m1_bars(self, count: int = 500) -> list[Bar]:
        """Bougies M1 CLÔTURÉES (position 1 : la position 0 est la bougie en cours)."""
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 1, count)
        if rates is None:
            return []
        point = float(self.mt5.symbol_info(self.symbol).point)
        return [
            Bar(self._to_utc(int(r["time"])), float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), float(r["spread"]) * point, float(r["tick_volume"]))
            for r in rates
        ]

    # ------------------------------------------------------------ compte
    def account(self) -> AccountState:
        a = self.mt5.account_info()
        if a is None:
            raise ConnectionError(f"account_info indisponible : {self.mt5.last_error()}")
        return AccountState(float(a.balance), float(a.equity), float(a.margin), str(a.currency))

    def positions(self) -> list[Position]:
        raw = self.mt5.positions_get(symbol=self.symbol) or ()
        out = []
        for p in raw:
            if p.magic != self.magic:
                continue  # positions manuelles ou d'un autre système : on n'y touche pas
            side = Side.BUY if p.type == self.mt5.POSITION_TYPE_BUY else Side.SELL
            pos = Position(
                position_id=str(p.ticket), symbol=p.symbol, side=side, volume=float(p.volume),
                entry_price=float(p.price_open), entry_ts=self._to_utc(p.time),
                stop_loss=float(p.sl), take_profit=float(p.tp) or None, strategy="",
                correlation_id="", client_order_id=str(p.comment), swap_accrued=float(p.swap),
            )
            out.append(pos)
        self._track(out)
        return out

    # ------------------------------------------------------------ ordres
    def _filling(self) -> int:
        fm = int(self.mt5.symbol_info(self.symbol).filling_mode)
        if fm & 1:
            return int(self.mt5.ORDER_FILLING_FOK)
        if fm & 2:
            return int(self.mt5.ORDER_FILLING_IOC)
        return int(self.mt5.ORDER_FILLING_RETURN)

    def _find_by_cid(self, cid: str) -> Position | None:
        return next((p for p in self.positions() if p.client_order_id == cid[:31]), None)

    def submit(self, intent: OrderIntent) -> OrderResult:
        cid = intent.client_order_id[:31]
        existing = self._find_by_cid(cid)
        if existing:  # idempotence : déjà exécuté (ex. relance après timeout)
            return OrderResult(True, cid, "déjà exécuté", fill=self._fill_from_pos(existing, intent))
        last_msg = ""
        for attempt in range(1, self.max_retries + 1):
            tick = self.mt5.symbol_info_tick(self.symbol)
            if tick is None:
                return OrderResult(False, cid, "pas de cotation", retryable=True)
            is_buy = intent.side is Side.BUY
            req = {
                "action": self.mt5.TRADE_ACTION_DEAL,
                "symbol": self.symbol,
                "volume": float(intent.volume),
                "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
                "price": float(tick.ask if is_buy else tick.bid),
                "sl": float(intent.stop_loss),
                "tp": float(intent.take_profit or 0.0),
                "deviation": self.deviation,
                "magic": self.magic,
                "comment": cid,
                "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self._filling(),
            }
            res = self.mt5.order_send(req)
            code = getattr(res, "retcode", None)
            if res is not None and code in OK_CODES:
                return self._after_fill(intent, cid, res, req["price"])
            last_msg = f"retcode={code} {getattr(res, 'comment', self.mt5.last_error())}"
            log.warning("mt5_order_failed", attempt=attempt, msg=last_msg)
            if res is None or code in RETRY_UNKNOWN:
                _time.sleep(1.0 * attempt)
                if (pos := self._find_by_cid(cid)) is not None:
                    return OrderResult(True, cid, "exécuté (vérifié après incident)",
                                       fill=self._fill_from_pos(pos, intent))
                continue
            if code in RETRY_PRICE:
                continue
            return OrderResult(False, cid, last_msg)  # non récupérable (stops invalides, marge...)
        return OrderResult(False, cid, f"abandon après {self.max_retries} essais : {last_msg}",
                           retryable=True)

    def _fill_from_pos(self, pos: Position, intent: OrderIntent) -> Fill:
        return Fill(pos.entry_ts, intent.client_order_id, pos.position_id, pos.symbol, pos.side,
                    pos.volume, pos.entry_price, 0.0, 0.0, True, intent.correlation_id)

    def _after_fill(self, intent: OrderIntent, cid: str, res: Any, requested: float) -> OrderResult:
        pos = self._find_by_cid(cid)
        if pos is None:
            return OrderResult(True, cid, "exécuté, position non encore visible")
        if not pos.stop_loss:
            log.error("mt5_position_without_sl", ticket=pos.position_id)
            if not self.modify_stops(pos.position_id, intent.stop_loss, intent.take_profit):
                self.close_position(pos.position_id, ExitReason.KILL_SWITCH, datetime.now(UTC))
                return OrderResult(False, cid, "SL impossible à poser : position fermée par sécurité")
        slippage = abs(float(res.price) - requested)
        fill = Fill(datetime.now(UTC), intent.client_order_id, pos.position_id, self.symbol, intent.side,
                    float(res.volume), float(res.price), 0.0, slippage, True, intent.correlation_id)
        return OrderResult(True, cid, "exécuté", fill=fill)

    def modify_stops(self, ticket: str, sl: float, tp: float | None) -> bool:
        res = self.mt5.order_send({
            "action": self.mt5.TRADE_ACTION_SLTP, "symbol": self.symbol, "position": int(ticket),
            "sl": float(sl), "tp": float(tp or 0.0), "magic": self.magic,
        })
        return res is not None and res.retcode in OK_CODES

    def close_position(self, position_id: str, reason: ExitReason, ts: datetime) -> OrderResult:
        pos = next((p for p in self.positions() if p.position_id == position_id), None)
        if pos is None:
            return OrderResult(False, "", "position introuvable")
        for attempt in range(1, self.max_retries + 1):
            tick = self.mt5.symbol_info_tick(self.symbol)
            is_buy = pos.side is Side.BUY
            res = self.mt5.order_send({
                "action": self.mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "position": int(position_id),
                "volume": pos.volume,
                "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
                "price": float(tick.bid if is_buy else tick.ask), "deviation": self.deviation * 3,
                "magic": self.magic, "comment": f"close:{reason.value}"[:31],
                "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(),
            })
            if res is not None and res.retcode in OK_CODES:
                return OrderResult(True, pos.client_order_id, f"fermée ({reason.value})")
            log.error("mt5_close_failed", attempt=attempt, retcode=getattr(res, "retcode", None))
            _time.sleep(0.5 * attempt)
        return OrderResult(False, pos.client_order_id, "échec de fermeture — INTERVENTION MANUELLE")

    # ------------------------------------------------------------ suivi des clôtures
    def _track(self, current: list[Position]) -> None:
        ids = {p.position_id for p in current}
        for pid, old in list(self._known.items()):
            if pid not in ids:
                self._closed.append(self._build_trade(old))
                del self._known[pid]
        for p in current:
            self._known.setdefault(p.position_id, p)

    def _build_trade(self, p: Position) -> Trade:
        deals = self.mt5.history_deals_get(position=int(p.position_id)) or ()
        out_deals = [d for d in deals if d.entry in (self.mt5.DEAL_ENTRY_OUT, self.mt5.DEAL_ENTRY_OUT_BY)]
        profit = sum(float(d.profit) for d in deals)
        commission = -sum(float(d.commission) + float(getattr(d, "fee", 0.0)) for d in deals)
        swap = sum(float(d.swap) for d in deals)
        last = out_deals[-1] if out_deals else None
        exit_price = float(last.price) if last else p.entry_price
        exit_ts = self._to_utc(last.time) if last else datetime.now(UTC)
        reason_code = getattr(last, "reason", None)
        reason = {getattr(self.mt5, "DEAL_REASON_SL", -1): ExitReason.STOP_LOSS,
                  getattr(self.mt5, "DEAL_REASON_TP", -2): ExitReason.TAKE_PROFIT}.get(reason_code,
                                                                                     ExitReason.MANUAL)
        return Trade(
            position_id=p.position_id, symbol=p.symbol, side=p.side, volume=p.volume, entry_ts=p.entry_ts,
            entry_price=p.entry_price, exit_ts=exit_ts, exit_price=exit_price, exit_reason=reason,
            gross_pnl=profit, commission=commission, swap=swap, pnl=profit - commission + swap,
            initial_risk=self.inst.risk_for(p.entry_price - p.stop_loss, p.volume) if p.stop_loss else 0.0,
            strategy=p.strategy, correlation_id=p.correlation_id,
        )

    def drain_closed_trades(self) -> list[Trade]:
        self.positions()  # met à jour le suivi
        out, self._closed = self._closed, []
        return out
