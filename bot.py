import os
import re
import threading
import sqlite3
import asyncio
import logging
import time
from collections import defaultdict
from functools import wraps
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from groq import AsyncGroq
from thefuzz import fuzz
from langdetect import detect, LangDetectException

# --- Logging structuré ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
TOKEN = os.environ.get('TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))
USE_WEBHOOK = os.environ.get('USE_WEBHOOK', 'false').lower() == 'true'
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')
PORT = int(os.environ.get('PORT', 8080))

# --- Client Groq ---
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
GROQ_MODEL = 'llama-3.1-8b-instant'

# --- Serveur Flask (keepalive pour Heroku/UptimeRobot) ---
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "I'm alive"

def run_flask():
    port = 8081 if USE_WEBHOOK else 8080
    flask_app.run(host='0.0.0.0', port=port)

def start_web_server():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

# --- Base de données SQLite ---
DB_PATH = 'jeffsbot.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        answer TEXT NOT NULL,
        suggester_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()
    logger.info("Base de données initialisée.")

def db_add_user(user_id: int, first_name: str, username: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (id, first_name, username) VALUES (?, ?, ?)',
              (user_id, first_name, username))
    conn.commit()
    conn.close()

def db_get_users() -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, first_name, username FROM users')
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'first_name': r[1], 'username': r[2]} for r in rows]

def db_add_suggestion(question: str, answer: str, suggester_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO suggestions (question, answer, suggester_id) VALUES (?, ?, ?)',
              (question, answer, suggester_id))
    conn.commit()
    conn.close()

def db_get_suggestions() -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, question, answer, suggester_id FROM suggestions')
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'question': r[1], 'answer': r[2], 'suggester': r[3]} for r in rows]

def db_delete_suggestion(suggestion_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM suggestions WHERE id = ?', (suggestion_id,))
    conn.commit()
    conn.close()

# --- Rate limiting ---
user_message_times: dict = defaultdict(list)
RATE_LIMIT = 10  # messages par minute

def is_rate_limited(user_id: int) -> bool:
    now = time.time()
    times = user_message_times[user_id]
    user_message_times[user_id] = [t for t in times if now - t < 60]
    if len(user_message_times[user_id]) >= RATE_LIMIT:
        return True
    user_message_times[user_id].append(now)
    return False

# --- Historique de conversation par utilisateur ---
conversation_history: dict = defaultdict(list)
MAX_HISTORY_PAIRS = 10  # 10 paires user/assistant = 20 messages max

def add_to_history(user_id: int, role: str, content: str):
    history = conversation_history[user_id]
    history.append({"role": role, "content": content})
    max_messages = MAX_HISTORY_PAIRS * 2
    if len(history) > max_messages:
        conversation_history[user_id] = history[-max_messages:]

# --- Parsing des FAQs et services similaires ---
FAQS: dict = {}
SIMILAR_SERVICES: dict = {}

def parse_markdown_faqs(file_path: str) -> dict:
    faqs = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        matches = re.findall(r'\*\*(.*?)\*\*\n(.*?)(?=\n\*\*|\Z)', content, re.DOTALL)
        for question, answer in matches:
            faqs[question.strip()] = answer.strip()
        logger.info(f"{len(faqs)} FAQs chargées depuis {file_path}.")
    except FileNotFoundError:
        logger.error(f"Fichier introuvable : {file_path}")
    except Exception as e:
        logger.error(f"Erreur lors du parsing des FAQs : {e}")
    return faqs

def parse_markdown_similar_services(file_path: str) -> dict:
    services = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        categories = re.split(r'##\s*(.*?)\n\n', content)[1:]
        for i in range(0, len(categories), 2):
            category_name = categories[i].strip()
            category_content = categories[i + 1].strip()
            service_matches = re.findall(
                r'\*\s*\*\*(.*?):\*\*\s*(.*?)(?=\n\*|\Z)', category_content, re.DOTALL
            )
            services[category_name] = {name.strip(): desc.strip() for name, desc in service_matches}
    except FileNotFoundError:
        logger.error(f"Fichier introuvable : {file_path}")
    except Exception as e:
        logger.error(f"Erreur lors du parsing des services similaires : {e}")
    return services

def build_faq_context() -> str:
    """Construit le contexte des FAQs pour Claude (mis en cache côté API)."""
    context = "=== FAQ de Jeff's Services ===\n"
    for question, answer in FAQS.items():
        context += f"Q: {question}\nR: {answer}\n\n"
    context += "\n=== Services Similaires ===\n"
    for category, services_dict in SIMILAR_SERVICES.items():
        context += f"\n{category}:\n"
        for name, desc in services_dict.items():
            context += f"- {name}: {desc}\n"
    return context

def find_answer_local(query: str) -> str:
    """Recherche floue dans les FAQs et services similaires. Retourne une réponse si score > 80."""
    query_lower = query.lower()
    best_score = 0
    best_answer = ""

    for question, answer in FAQS.items():
        score = fuzz.partial_ratio(query_lower, question.lower())
        if score > best_score:
            best_score = score
            best_answer = answer

    for category, services_dict in SIMILAR_SERVICES.items():
        score = fuzz.partial_ratio(query_lower, category.lower())
        if score > best_score:
            best_score = score
            response = f"Services dans {category}:\n"
            for name, desc in services_dict.items():
                response += f"- {name}: {desc}\n"
            best_answer = response
        for name, desc in services_dict.items():
            score = fuzz.partial_ratio(query_lower, name.lower())
            if score > best_score:
                best_score = score
                best_answer = f"{name}: {desc}"

    if best_score > 80:
        return best_answer
    return ""

async def get_ai_response(user_id: int, prompt: str, lang: str = 'en') -> str:
    """Appel à Groq (Llama 3.1) avec historique de conversation."""
    try:
        if lang == 'fr':
            system_text = (
                "Tu es un assistant service client sympathique et professionnel pour Jeff's Services, "
                "une entreprise de solutions numériques. Réponds aux questions sur les services de l'entreprise "
                "de manière concise. Réponds toujours en français si la question est en français.\n\n"
            )
        else:
            system_text = (
                "You are a friendly and professional customer support assistant for Jeff's Services, "
                "a digital solutions company. Answer questions about the company's services concisely. "
                "Reply in English if the question is in English.\n\n"
            )
        system_text += build_faq_context()

        history = conversation_history[user_id]
        messages = (
            [{"role": "system", "content": system_text}]
            + history
            + [{"role": "user", "content": prompt}]
        )

        response = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=400,
            temperature=0.7,
        )

        answer = response.choices[0].message.content.strip()
        logger.info(
            f"Groq [{user_id}] — tokens: {response.usage.prompt_tokens} in, "
            f"{response.usage.completion_tokens} out"
        )
        return answer

    except Exception as e:
        logger.error(f"Erreur Groq pour user {user_id}: {e}")
        if lang == 'fr':
            return "Désolé, une erreur est survenue. Veuillez réessayer plus tard."
        return "I'm sorry, an error occurred. Please try again later."

# --- Décorateurs ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("Désolé, cette commande est réservée à l'administrateur.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Handlers Telegram ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db_add_user(user.id, user.first_name, user.username)
    logger.info(f"Utilisateur démarré : {user.first_name} (ID: {user.id})")
    start_message = (
        f"👋 Bonjour, {user.mention_html()} !\n\n"
        "Je suis le bot de Jeff's Services. Je peux répondre à vos questions sur nos services et nos FAQs.\n\n"
        "Vous pouvez m'aider à m'améliorer ! Si je ne connais pas la réponse, "
        "vous pouvez me l'apprendre avec la commande /suggest.\n\n"
        "<b>Comment suggérer une nouvelle réponse :</b>\n"
        "<code>/suggest Votre Question ? == La Réponse Correcte</code>\n\n"
        "Tapez /help pour voir toutes les commandes."
    )
    await update.message.reply_html(start_message)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_message = update.message.text

    if is_rate_limited(user.id):
        await update.message.reply_text(
            "Vous envoyez trop de messages. Veuillez patienter une minute."
            if 'fr' in user_message[:20].lower() else
            "You're sending too many messages. Please wait a minute."
        )
        return

    try:
        lang = detect(user_message)
    except LangDetectException:
        lang = 'en'

    # Recherche locale d'abord (rapide, gratuit)
    answer = find_answer_local(user_message)

    if not answer:
        # Fallback sur Claude avec historique de conversation
        answer = await get_ai_response(user.id, user_message, lang)
        add_to_history(user.id, "user", user_message)
        add_to_history(user.id, "assistant", answer)

    await update.message.reply_text(answer)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    help_text = (
        "**Voici ce que vous pouvez faire :**\n\n"
        "❓ **Poser une question** — Envoyez simplement votre question.\n\n"
        "💡 **Suggérer une réponse :**\n"
        "*/suggest Votre Question ? == La Réponse*\n\n"
        "🔄 **/clear** — Réinitialiser votre historique de conversation\n\n"
        "🤖 **/start** — Voir le message de bienvenue"
    )
    if user_id == ADMIN_ID:
        help_text += (
            "\n\n--- **Commandes Admin** ---\n\n"
            "📊 **/stats** — Statistiques du bot\n"
            "🔍 **/review** — Gérer les suggestions\n"
            "✉️ **/send [user_id] [message]** — Message privé\n"
            "📢 **/broadcast [message]** — Diffuser à tous\n"
            "🔄 **/reload** — Recharger les FAQs depuis les fichiers"
        )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("✅ Votre historique de conversation a été effacé.")

async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /suggest Votre Question? == La Réponse Correcte.")
        return
    suggestion_text = ' '.join(args)
    if '==' not in suggestion_text:
        await update.message.reply_text("Format incorrect. Utilisez '==' pour séparer la question de la réponse.")
        return
    question, answer = suggestion_text.split('==', 1)
    question, answer = question.strip(), answer.strip()
    if not question or not answer:
        await update.message.reply_text("La question et la réponse ne peuvent pas être vides.")
        return
    db_add_suggestion(question, answer, update.effective_user.id)
    logger.info(f"Nouvelle suggestion de {update.effective_user.id}: {question}")
    await update.message.reply_text("Merci pour votre suggestion ! Elle sera examinée par un administrateur.")

@admin_only
async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    suggestions = db_get_suggestions()
    if not suggestions:
        await update.message.reply_text("Aucune suggestion en attente.")
        return
    await update.message.reply_text(f"{len(suggestions)} suggestion(s) en attente :")
    for s in suggestions:
        keyboard = [[
            InlineKeyboardButton("✅ Approuver", callback_data=f"approve_{s['id']}"),
            InlineKeyboardButton("❌ Rejeter", callback_data=f"reject_{s['id']}"),
        ]]
        await update.message.reply_text(
            f"*Suggestion #{s['id']}*\n"
            f"*Question:* {s['question']}\n"
            f"*Réponse:* {s['answer']}\n"
            f"_Suggérée par:_ `{s['suggester']}`",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

@admin_only
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, suggestion_id_str = query.data.split('_', 1)
    suggestion_id = int(suggestion_id_str)
    suggestions = db_get_suggestions()
    suggestion = next((s for s in suggestions if s['id'] == suggestion_id), None)
    if not suggestion:
        await query.edit_message_text(text=f"La suggestion #{suggestion_id} a déjà été traitée.")
        return
    if action == 'approve':
        with open('faqs.md', 'a', encoding='utf-8') as f:
            f.write(f"\n**{suggestion['question']}**\n{suggestion['answer']}\n")
        global FAQS
        FAQS = parse_markdown_faqs('faqs.md')
        logger.info(f"Suggestion #{suggestion_id} approuvée et ajoutée aux FAQs.")
        await query.edit_message_text(text=f"✅ Suggestion #{suggestion_id} approuvée et ajoutée aux FAQs.")
    elif action == 'reject':
        logger.info(f"Suggestion #{suggestion_id} rejetée.")
        await query.edit_message_text(text=f"❌ Suggestion #{suggestion_id} rejetée.")
    db_delete_suggestion(suggestion_id)

@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_to_send = ' '.join(context.args)
    if not message_to_send:
        await update.message.reply_text("Usage: /broadcast Votre message. Utilisez {nom} pour le prénom.")
        return
    users = db_get_users()
    await update.message.reply_text(f"Diffusion à {len(users)} utilisateurs...")
    success_count, failure_count = 0, 0
    for user in users:
        try:
            personalized = message_to_send.replace('{nom}', user.get('first_name', ''))
            await context.bot.send_message(chat_id=user['id'], text=personalized)
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            failure_count += 1
            logger.warning(f"Diffusion échouée pour {user['id']}: {e}")
    await update.message.reply_text(
        f"Diffusion terminée.\n✅ Succès : {success_count}\n❌ Échecs : {failure_count}"
    )

@admin_only
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /send [user_id] [votre message]")
        return
    try:
        target_id = int(args[0])
    except ValueError:
        await update.message.reply_text("L'ID utilisateur doit être un nombre.")
        return
    message_to_send = ' '.join(args[1:])
    try:
        await context.bot.send_message(chat_id=target_id, text=message_to_send)
        await update.message.reply_text(f"✅ Message envoyé à {target_id}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Impossible d'envoyer à {target_id}. Erreur: {e}")

@admin_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = db_get_users()
    suggestions = db_get_suggestions()
    active_conversations = sum(1 for h in conversation_history.values() if h)
    stats_message = (
        "📊 **Statistiques du Bot :**\n\n"
        f"👥 **Utilisateurs enregistrés :** {len(users)}\n"
        f"💡 **Suggestions en attente :** {len(suggestions)}\n"
        f"📚 **Entrées FAQ :** {len(FAQS)}\n"
        f"💬 **Conversations actives :** {active_conversations}\n"
        f"🤖 **Modèle IA :** {GROQ_MODEL}\n"
    )
    await update.message.reply_text(stats_message, parse_mode='Markdown')

@admin_only
async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global FAQS, SIMILAR_SERVICES
    FAQS = parse_markdown_faqs('faqs.md')
    SIMILAR_SERVICES = parse_markdown_similar_services('similar_services.md')
    logger.info("FAQs et services rechargés par l'admin.")
    await update.message.reply_text(f"✅ FAQs rechargées. {len(FAQS)} entrées chargées.")

def main() -> None:
    start_web_server()
    init_db()

    global FAQS, SIMILAR_SERVICES
    FAQS = parse_markdown_faqs('faqs.md')
    SIMILAR_SERVICES = parse_markdown_similar_services('similar_services.md')
    logger.info(f"Bot démarré — {len(FAQS)} FAQs, {len(SIMILAR_SERVICES)} catégories de services.")

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("suggest", suggest_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("reload", reload_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    if USE_WEBHOOK and WEBHOOK_URL:
        logger.info(f"Démarrage en mode webhook sur le port {PORT}")
        application.run_webhook(
            listen='0.0.0.0',
            port=PORT,
            url_path=TOKEN,
            webhook_url=f'{WEBHOOK_URL}/{TOKEN}'
        )
    else:
        logger.info("Démarrage en mode polling...")
        application.run_polling()

if __name__ == "__main__":
    main()
