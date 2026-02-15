# Déploiement Home Lab (VM + Containers)

## 1) Pré-requis

- Une VM Linux (Ubuntu/Debian recommandé)
- Docker + docker-compose installés
- Optionnel: un reverse proxy (Nginx, Traefik, Caddy)

---

## 2) Option recommandée: déploiement en container

Dans le dossier `sondage_clone`:

1. Copie le fichier d'environnement:

   `cp .env.server.example .env`

2. Mets une vraie clé secrète dans `.env`:

   `SONDAGE_SECRET_KEY=...`

   Et vérifie les paramètres sécurité:

   - `SONDAGE_AUTH_ENABLED=1`
   - `SONDAGE_COOKIE_SECURE=1`
   - `SONDAGE_COOKIE_SAMESITE=Strict`
   - `FEEDBACK_TO_EMAIL=ton-email@domaine.com` (optionnel, sinon `SMTP_FROM_EMAIL`)

3. Lance l'application:

   `docker-compose up -d --build`

4. Vérifie l'état:

   `docker-compose ps`

5. Ouvre:

   `http://IP_DE_TA_VM:5050`

6. Création du premier compte:

   - Mets temporairement `SONDAGE_AUTH_ALLOW_REGISTRATION=1`
   - Crée ton compte via `/auth/register`
   - Puis repasse `SONDAGE_AUTH_ALLOW_REGISTRATION=0` et redémarre

### Variante production avec reverse proxy

- Le `docker-compose.yml` expose l'app seulement en local VM: `127.0.0.1:5050`
- L'accès externe se fait via Nginx + HTTPS (sections suivantes)

### Persistance

- La base SQLite est conservée dans le volume Docker `sondage_data`.

---

## 3) Option VM sans container (service systemd)

Dans le dossier projet racine:

1. Installe les dépendances:

   `pip install -r requirements.txt`

2. Variables d'environnement minimales:

   - `SONDAGE_SECRET_KEY` (obligatoire)
   - `SONDAGE_DB_PATH` (ex: `/opt/sondage/data/sondage.db`)
   - `PORT=5050`
   - `FLASK_DEBUG=0`

3. Démarrage manuel:

   `gunicorn --chdir sondage_clone --bind 0.0.0.0:5050 --workers 2 --threads 4 app:app`

4. Option service systemd: crée un service qui lance la commande ci-dessus.

---

## 4) Sécurité conseillée en home lab

- Mets l'app derrière HTTPS (reverse proxy + certificat)
- Si HTTPS est actif, passe `SONDAGE_COOKIE_SECURE=1`
- Ouvre seulement les ports nécessaires sur ton firewall
- Sauvegarde régulière de la base SQLite

---

## 5) Domaine + SSL (ton cas)

Objectif: publier

- `https://sondage.noschoixpourvous.com`
- `https://www.sondage.noschoixpourvous.com`

### 5.1 DNS

Dans ton DNS public, crée 2 enregistrements `A` vers l'IP publique de ton home lab:

- `sondage.noschoixpourvous.com`
- `www.sondage.noschoixpourvous.com`

### 5.2 NAT / pare-feu

Redirige vers la VM:

- TCP 80 -> VM:80
- TCP 443 -> VM:443

### 5.3 Démarrer l'app

Dans `sondage_clone`:

- `cp .env.server.example .env`
- Mets une vraie valeur dans `SONDAGE_SECRET_KEY`
- `docker-compose up -d --build`

### 5.4 Installer Nginx + Certbot (sur la VM)

Ubuntu/Debian:

- `sudo apt update`
- `sudo apt install -y nginx certbot python3-certbot-nginx`

### 5.5 Installer la config Nginx du projet

- Copie [sondage_clone/deploy/nginx/sondage.noschoixpourvous.com.conf](sondage_clone/deploy/nginx/sondage.noschoixpourvous.com.conf) vers:
   `/etc/nginx/sites-available/sondage.noschoixpourvous.com.conf`

- Active le site:

   - `sudo ln -s /etc/nginx/sites-available/sondage.noschoixpourvous.com.conf /etc/nginx/sites-enabled/`
   - `sudo nginx -t`
   - `sudo systemctl reload nginx`

### 5.6 Générer le certificat SSL

- `sudo certbot --nginx -d sondage.noschoixpourvous.com -d www.sondage.noschoixpourvous.com --redirect -m ton-email@domaine.com --agree-tos -n`

### 5.7 Vérifier

- `https://sondage.noschoixpourvous.com`
- `https://www.sondage.noschoixpourvous.com` (redirige vers le domaine principal)

### 5.8 Renouvellement auto

- `systemctl status certbot.timer`
- Test de renouvellement: `sudo certbot renew --dry-run`

---

## 6) Sauvegarde / restauration

### Container

- Sauvegarde volume:

   `docker run --rm -v sondage_clone_sondage_data:/data -v $(pwd):/backup alpine tar czf /backup/sondage_data_backup.tar.gz -C /data .`

### VM

- Sauvegarde le fichier SQLite défini par `SONDAGE_DB_PATH`.
