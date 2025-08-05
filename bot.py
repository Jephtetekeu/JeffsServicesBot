import os
import re
import openai
import threading
import json
import asyncio
from functools import wraps
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from openai import AsyncOpenAI
from thefuzz import fuzz
from langdetect import detect, LangDetectException

# --- Configuration du serveur web pour UptimeRobot ---
app = Flask('')

@app.route('/')
def home():
    return "I'm alive"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def start_web_server():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

# --- Configuration de l'apprentissage et de la diffusion ---
SUGGESTIONS_FILE = 'suggestions.json'
USERS_FILE = 'users.json'
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("Désolé, cette commande est réservée à l'administrateur.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

def load_json_file(file_path: str) -> list | dict:
    if not os.path.exists(file_path):
        return [] if file_path.endswith('s.json') else {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return [] if file_path.endswith('s.json') else {}

def save_json_file(data, file_path: str):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- Le reste du code du bot ---

TOKEN = os.environ.get('TOKEN')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

FAQS = {}
SIMILAR_SERVICES = {}

def parse_markdown_faqs(file_path: str) -> dict:
    faqs = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        matches = re.findall(r'\*\*(.*?)\*\*\n(.*?)(?=\n\*\*|\Z)', content, re.DOTALL)
        for question, answer in matches:
            faqs[question.strip()] = answer.strip()
    except FileNotFoundError:
        print(f"Error: The file at {file_path} was not found.")
    except Exception as e:
        print(f"An error occurred while parsing FAQs: {e}")
    return faqs

def parse_markdown_similar_services(file_path: str) -> dict:
    services = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        categories = re.split(r'##\s*(.*?)\n\n', content)[1:]
        for i in range(0, len(categories), 2):
            category_name = categories[i].strip()
            category_content = categories[i+1].strip()
            service_matches = re.findall(r'\*\s*\*\*(.*?):\*\*\s*(.*?)(?=\n\*|\Z)', category_content, re.DOTALL)
            services[category_name] = {name.strip(): desc.strip() for name, desc in service_matches}
    except FileNotFoundError:
        print(f"Error: The file at {file_path} was not found.")
    except Exception as e:
        print(f"An error occurred while parsing similar services: {e}")
    return services

def find_answer(query: str, lang: str = 'en') -> str:
    query_lower = query.lower()
    best_match_score = 0
    best_answer = ""
    for question, answer in FAQS.items():
        score = fuzz.partial_ratio(query_lower, question.lower())
        if score > best_match_score:
            best_match_score = score
            best_answer = f"FAQ: {answer}"
    for category, services_dict in SIMILAR_SERVICES.items():
        if fuzz.partial_ratio(query_lower, category.lower()) > best_match_score:
            best_match_score = fuzz.partial_ratio(query_lower, category.lower())
            response = f"Services in {category}:\n"
            for name, desc in services_dict.items():
                response += f"- {name}: {desc}\n"
            best_answer = response
        for name, desc in services_dict.items():
            if fuzz.partial_ratio(query_lower, name.lower()) > best_match_score:
                best_match_score = fuzz.partial_ratio(query_lower, name.lower())
                best_answer = f"Similar Service: {name} - {desc}"
    if best_match_score > 80:
        return best_answer
    if lang == 'fr':
        return "Désolé, je n'ai pas trouvé de réponse. Pourriez-vous reformuler ? Vous pouvez aussi suggérer une nouvelle réponse avec /suggest."
    return "I'm sorry, I couldn't find an answer. Could you rephrase? You can also suggest a new answer with /suggest."

async def get_openai_response(prompt: str, lang: str = 'en') -> str:
    try:
        system_message = "You are a helpful assistant for Jeff's Services."
        if lang == 'fr':
            system_message = "Vous êtes un assistant utile pour Jeff\'s Services."
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Error getting response from OpenAI: {e}")
        if lang == 'fr':
            return "Désolé, une erreur est survenue. Veuillez réessayer plus tard."
        return "I'm sorry, an error occurred. Please try again later."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    users = load_json_file(USERS_FILE)
    if user.id not in [u['id'] for u in users]:
        users.append({'id': user.id, 'first_name': user.first_name, 'username': user.username})
        save_json_file(users, USERS_FILE)
        print(f"Nouvel utilisateur enregistré : {user.first_name} (ID: {user.id})")

    start_message = f"""👋 Bonjour, {user.mention_html()} !\n\nJe suis le bot de Jeff's Services. Je peux répondre à vos questions sur nos services et nos FAQs.\n\nVous pouvez m'aider à m'améliorer ! Si je ne connais pas la réponse à une question, vous pouvez me l'apprendre avec la commande /suggest.\n\n<b>Comment suggérer une nouvelle réponse :</b>\n<code>/suggest Votre Question ? == La Réponse Correcte</code>\n\nPour voir toutes les commandes disponibles, tapez /help."""
    await update.message.reply_html(start_message)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    try:
        lang = detect(user_message)
    except LangDetectException:
        lang = 'en'
    answer = find_answer(user_message, lang)
    if "Désolé" in answer or "I'm sorry" in answer:
        openai_answer = await get_openai_response(user_message, lang)
        if openai_answer:
            answer = openai_answer
    await update.message.reply_text(answer)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    help_text_user = """
**Voici ce que vous pouvez faire :**

❓ **Poser une question :**
Envoyez-moi simplement votre question et j'essaierai d'y répondre.

💡 **Suggérer une nouvelle réponse :**
Si je ne connais pas la réponse, vous pouvez me l'apprendre.
*/suggest Votre Question ? == La Réponse Correcte*

🤖 **À propos de moi :**
Utilisez /start pour voir mon message de bienvenue."""

    help_text_admin = """
--- **Commandes Administrateur** ---

📊 **/stats**
Afficher les statistiques du bot (utilisateurs, suggestions, FAQs).

🔍 **/review**
Voir, approuver ou rejeter les suggestions.

✉️ **/send [user_id] [message]**
Envoyer un message privé à un utilisateur.

📢 **/broadcast [message]**
Envoyer un message à tous les utilisateurs."""

    full_help_text = help_text_user
    if user_id == ADMIN_ID:
        full_help_text += help_text_admin

    await update.message.reply_text(full_help_text, parse_mode='MarkdownV2')

async def suggest_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /suggest Votre Question? == La Réponse Correcte.")
        return
    suggestion_text = ' '.join(args)
    if '==' not in suggestion_text:
        await update.message.reply_text("Format incorrect. Veuillez utiliser '==' pour séparer la question de la réponse.")
        return
    question, answer = suggestion_text.split('==', 1)
    question = question.strip()
    answer = answer.strip()
    if not question or not answer:
        await update.message.reply_text("La question et la réponse ne peuvent pas être vides.")
        return
    suggestions = load_json_file(SUGGESTIONS_FILE)
    suggestion_id = len(suggestions) + 1
    suggestions.append({
        'id': suggestion_id,
        'question': question,
        'answer': answer,
        'suggester': update.effective_user.id
    })
    save_json_file(suggestions, SUGGESTIONS_FILE)
    await update.message.reply_text("Merci pour votre suggestion ! Elle sera examinée par un administrateur.")

@admin_only
async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    suggestions = load_json_file(SUGGESTIONS_FILE)
    if not suggestions:
        await update.message.reply_text("Il n'y a aucune suggestion en attente.")
        return
    await update.message.reply_text("Voici les suggestions en attente :")
    for s in suggestions:
        keyboard = [
            [
                InlineKeyboardButton("Approuver", callback_data=f"approve_{s['id']}"),
                InlineKeyboardButton("Rejeter", callback_data=f"reject_{s['id']}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Suggestion #{s['id']}\n"
            f"**Question:** {s['question']}\n"
            f"**Réponse:** {s['answer']}\n"
            f"Suggérée par l'utilisateur ID: `{s['suggester']}`",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

@admin_only
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, suggestion_id_str = query.data.split('_', 1)
    suggestion_id = int(suggestion_id_str)
    suggestions = load_json_file(SUGGESTIONS_FILE)
    suggestion_to_process = next((s for s in suggestions if s['id'] == suggestion_id), None)
    if not suggestion_to_process:
        await query.edit_message_text(text=f"La suggestion #{suggestion_id} a déjà été traitée.")
        return
    suggestions = [s for s in suggestions if s['id'] != suggestion_to_process['id']]
    if action == 'approve':
        with open('faqs.md', 'a', encoding='utf-8') as f:
            f.write(f"\n**{suggestion_to_process['question']}**\n{suggestion_to_process['answer']}\n")
        global FAQS
        FAQS = parse_markdown_faqs('faqs.md')
        await query.edit_message_text(text=f"✅ Suggestion #{suggestion_id} approuvée et ajoutée.")
    elif action == 'reject':
        await query.edit_message_text(text=f"❌ Suggestion #{suggestion_id} rejetée.")
    save_json_file(suggestions, SUGGESTIONS_FILE)

@admin_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_to_send = ' '.join(context.args)
    if not message_to_send:
        await update.message.reply_text("Usage: /broadcast Votre message ici. Utilisez {nom} pour le prénom.")
        return

    users = load_json_file(USERS_FILE)
    await update.message.reply_text(f"Début de la diffusion à {len(users)} utilisateurs...")
    
    success_count = 0
    failure_count = 0

    for user in users:
        try:
            personalized_message = message_to_send.replace('{nom}', user.get('first_name', ''))
            await context.bot.send_message(chat_id=user['id'], text=personalized_message)
            success_count += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            failure_count += 1
            print(f"Erreur lors de l'envoi à {user['id']}: {e}")

    await update.message.reply_text(f"Diffusion terminée.\nSuccès : {success_count}\nÉchecs : {failure_count}")

@admin_only
async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /send [user_id] [votre message]")
        return

    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("L'ID de l'utilisateur doit être un nombre.")
        return

    message_to_send = ' '.join(args[1:])

    try:
        await context.bot.send_message(chat_id=user_id, text=message_to_send)
        await update.message.reply_text(f"Message envoyé avec succès à l'utilisateur {user_id}.")
    except Exception as e:
        await update.message.reply_text(f"Impossible d'envoyer le message à {user_id}. Erreur: {e}")

@admin_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    users = load_json_file(USERS_FILE)
    suggestions = load_json_file(SUGGESTIONS_FILE)
    faqs_count = len(FAQS) # FAQS est déjà chargé en global

    stats_message = (
        "📊 **Statistiques du Bot :**\n\n"
        f"👥 **Utilisateurs enregistrés :** {len(users)}\n"
        f"💡 **Suggestions en attente :** {len(suggestions)}\n"
        f"📚 **Questions/Réponses dans la FAQ :** {faqs_count}\n"
    )
    await update.message.reply_text(stats_message, parse_mode='Markdown')

def main() -> None:
    start_web_server()

    global FAQS, SIMILAR_SERVICES
    FAQS = parse_markdown_faqs('faqs.md')
    SIMILAR_SERVICES = parse_markdown_similar_services('similar_services.md')

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("suggest", suggest_command))
    application.add_handler(CommandHandler("review", review_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    application.run_polling()

if __name__ == "__main__":
    main()
