# Application Python météo du monde

Ce projet contient deux versions d'une application météo mondiale :

- **Desktop (Tkinter)** : interface locale simple.
- **Web (Flask)** : interface moderne, responsive, avec autocomplétion des villes.

Il contient aussi une application de sondage style Sondage :

- **Sondage Clone (Flask + SQLite)** : création de sondage, partage de lien, votes et résultats.

## Fonctionnalités

- Recherche de ville mondiale (ex: Paris, Tokyo, Lagos, Montréal)
- Autocomplétion des villes dès 2 caractères
- Température actuelle, humidité, vent et description météo
- Prévisions météo des 5 prochains jours

## Installation

1. Ouvre un terminal dans le dossier du projet.
2. Installe les dépendances :

```bash
pip install -r requirements.txt
```

## Lancement

### Version Desktop

```bash
python app.py
```

### Version Web (recommandée)

```bash
python web_app.py
```

Puis ouvre `http://127.0.0.1:5000` dans ton navigateur.

### Version Sondage (Sondage clone)

```bash
python sondage_clone/app.py
```

Puis ouvre `http://127.0.0.1:5050` dans ton navigateur.

### Déploiement Home Lab (VM / Docker)

Voir le guide: `sondage_clone/DEPLOYMENT.md`

## GitHub Actions

Le repo contient 2 workflows:

- `CI` : vérifie la syntaxe Python et build l'image Docker.
- `Deploy Sondage` : déploie `sondage_clone` sur ta VM via SSH.

Secrets GitHub à créer dans le repo:

- `VM_HOST` : IP ou domaine de la VM
- `VM_USER` : utilisateur SSH
- `VM_SSH_KEY` : clé privée SSH
- `VM_PORT` : port SSH (optionnel, sinon 22)
- `VM_PROJECT_DIR` : chemin du repo sur la VM (ex: `/opt/sondage`)

## API utilisée

- Géocodage + météo : Open-Meteo (gratuit, sans clé API)
