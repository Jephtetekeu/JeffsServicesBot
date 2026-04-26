# 🤖 Jeff's Services Bot

Bot Telegram de support client pour [jeffsservices.com](https://jeffsservices.com), capable de répondre aux questions fréquentes en **français et en anglais** grâce à une base de connaissances locale et à l'IA **Llama 3.1 via Groq** en fallback.

---

## ✨ Fonctionnalités

- 🌍 **Bilingue FR/EN** — détection automatique de la langue
- 🔍 **Recherche locale** — matching flou sur les FAQs intégrées (réponse instantanée, sans coût IA)
- 🧠 **IA Llama 3.1 (Groq)** — fallback intelligent avec mémoire de conversation par utilisateur
- 🗄️ **Base de données SQLite** — gestion des utilisateurs et suggestions
- 🛡️ **Rate limiting** — protection anti-spam (10 messages/minute par utilisateur)
- 💬 **Historique de conversation** — le bot mémorise les 10 derniers échanges par utilisateur
- 📢 **Diffusion** — l'admin peut envoyer un message à tous les utilisateurs
- 💡 **Suggestions collaboratives** — les utilisateurs peuvent proposer de nouvelles réponses, l'admin les valide

---

## 🛠️ Stack technique

| Composant | Technologie |
|---|---|
| Framework bot | `python-telegram-bot 22.3` |
| IA fallback | `Llama 3.1 8B Instant` via [Groq](https://groq.com) — **gratuit** |
| Matching FAQ | `thefuzz` + `python-levenshtein` |
| Détection langue | `langdetect` |
| Base de données | `SQLite` |
| Serveur keepalive | `Flask` |
| Hébergement | [Render](https://render.com) |

---

## 📋 Commandes

### Utilisateurs
| Commande | Description |
|---|---|
| `/start` | Message de bienvenue |
| `/help` | Liste des commandes |
| `/clear` | Réinitialiser l'historique de conversation |
| `/suggest Question? == Réponse` | Proposer une nouvelle réponse |

### Admin uniquement
| Commande | Description |
|---|---|
| `/stats` | Statistiques du bot |
| `/review` | Voir et valider les suggestions |
| `/broadcast [message]` | Envoyer un message à tous les utilisateurs |
| `/send [user_id] [message]` | Envoyer un message privé à un utilisateur |
| `/reload` | Recharger les FAQs sans redémarrer |

---

## 🚀 Installation locale

### Prérequis
- Python 3.10+
- Un bot Telegram (créé via [@BotFather](https://t.me/BotFather))
- Une clé API Groq **gratuite** ([console.groq.com](https://console.groq.com))

### Étapes

**1. Cloner le dépôt**
```bash
git clone https://github.com/ton-username/JeffsServicesBot.git
cd JeffsServicesBot
```

**2. Installer les dépendances**
```bash
pip install -r requirements.txt
```

**3. Configurer les variables d'environnement**
```bash
cp .env.example .env
# Remplir .env avec tes vraies valeurs
```

**4. Lancer le bot**
```bash
python bot.py
```

---

## ☁️ Déploiement sur Render

1. Créer un compte sur [render.com](https://render.com)
2. **New → Web Service** → connecter le dépôt GitHub
3. Configurer le service :
   - **Runtime :** Python 3
   - **Build command :** `pip install -r requirements.txt`
   - **Start command :** `python bot.py`
4. Ajouter les variables d'environnement dans l'onglet **Environment** :

| Variable | Valeur |
|---|---|
| `TOKEN` | Token Telegram Bot |
| `GROQ_API_KEY` | Clé API Groq (gratuite sur console.groq.com) |
| `ADMIN_ID` | Ton ID Telegram |
| `USE_WEBHOOK` | `true` |
| `WEBHOOK_URL` | URL fournie par Render (ex: `https://ton-app.onrender.com`) |
| `PORT` | `8080` |

5. **Deploy** — Render détecte les changements GitHub et redéploie automatiquement.

> **Note :** Sur le tier gratuit, le service s'endort après 15 minutes d'inactivité. Utilise [UptimeRobot](https://uptimerobot.com) pour envoyer un ping régulier sur l'URL Render et maintenir le bot actif.

---

## 📁 Structure du projet

```
JeffsServicesBot/
├── bot.py               # Code principal du bot
├── faqs.md              # Base de connaissances FAQ (FR/EN)
├── similar_services.md  # Services similaires
├── requirements.txt     # Dépendances Python
├── Procfile             # (héritage Heroku, inutilisé sur Render)
├── Dockerfile           # Docker optionnel
├── .env.example         # Modèle des variables d'environnement
└── jeffsbot.db          # Base SQLite (créée automatiquement, non versionnée)
```

---

## 💡 Ajouter des FAQs

Les FAQs sont dans [faqs.md](faqs.md). Le format est :

```markdown
**Votre question ici ?**
La réponse complète ici.
```

Après modification, utilise la commande `/reload` (admin) pour recharger sans redémarrer, ou redémarre le bot.

---

## 🔐 Variables d'environnement

Voir [.env.example](.env.example) pour la liste complète.  
Ne jamais committer le fichier `.env` — il est inclus dans `.gitignore`.

---

## 📄 Licence

Ce projet est privé et développé pour [Jeff's Services](https://jeffsservices.com).
