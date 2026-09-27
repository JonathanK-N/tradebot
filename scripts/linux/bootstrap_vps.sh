#!/usr/bin/env bash
# Durcissement + installation d'un VPS Linux neuf (Ubuntu 24.04 / Debian 12).
# À lancer UNE fois, en root, depuis Termius :
#   curl -fsSL https://raw.githubusercontent.com/<toi>/tradebot/main/scripts/linux/bootstrap_vps.sh -o b.sh
#   less b.sh            # TOUJOURS relire un script avant de l'exécuter en root
#   bash b.sh "ssh-ed25519 AAAA... ta-cle-publique"
#
# Résultat : utilisateur `tradebot` (sudo, clé SSH uniquement), root et mots de passe SSH
# désactivés, pare-feu qui bloque TOUT l'entrant sauf Tailscale, mises à jour de sécurité
# automatiques, fail2ban, Docker, Tailscale.
set -euo pipefail

PUBKEY="${1:?Usage: bash bootstrap_vps.sh \"<clé publique SSH>\"}"
USER_NAME=tradebot

echo "==> Paquets de base"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git ufw fail2ban \
  unattended-upgrades restic jq

echo "==> Utilisateur $USER_NAME"
id "$USER_NAME" &>/dev/null || adduser --disabled-password --gecos "" "$USER_NAME"
usermod -aG sudo "$USER_NAME"
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" /home/$USER_NAME/.ssh
echo "$PUBKEY" > /home/$USER_NAME/.ssh/authorized_keys
chown "$USER_NAME:$USER_NAME" /home/$USER_NAME/.ssh/authorized_keys
chmod 600 /home/$USER_NAME/.ssh/authorized_keys
echo "$USER_NAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$USER_NAME
chmod 440 /etc/sudoers.d/90-$USER_NAME

echo "==> SSH : clé uniquement, pas de root"
cat > /etc/ssh/sshd_config.d/99-hardening.conf <<'CONF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
MaxAuthTries 3
X11Forwarding no
AllowUsers tradebot
CONF
systemctl restart ssh || systemctl restart sshd

echo "==> Mises à jour de sécurité automatiques"
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now fail2ban

echo "==> Docker"
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
usermod -aG docker "$USER_NAME"

echo "==> Tailscale"
if ! command -v tailscale &>/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo ">>> Authentifie ce serveur sur ton tailnet (ouvre le lien sur ton téléphone) :"
tailscale up --ssh=false

echo "==> Pare-feu : SSH temporairement ouvert, tout le reste passe par Tailscale"
ufw default deny incoming
ufw default allow outgoing
ufw allow in on tailscale0
ufw allow 22/tcp   # À FERMER une fois l'accès SSH via Tailscale vérifié (voir ci-dessous)
ufw --force enable

TS_IP=$(tailscale ip -4 | head -1)
cat <<MSG

✅ Terminé. IP Tailscale de ce serveur : $TS_IP

Étapes suivantes (depuis Termius) :
 1. Reconnecte-toi en  ssh tradebot@$TS_IP  (via l'app Tailscale active sur ton téléphone).
 2. Si ça marche, ferme SSH public :  sudo ufw delete allow 22/tcp
 3. Continue avec docs/DEPLOIEMENT.md (étape « Déployer le cœur »).
MSG
