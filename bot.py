from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import re
import google.generativeai as genai

# Replace with your bot's token from BotFather
TOKEN = '8480651289:AAGJfP3LOke-rDiB605jj3U5fODFJ3T7Xm4' # Your Telegram Bot API Key
GEMINI_API_KEY = 'AIzaSyCzFim9Qp0E1w9kf-pcc-7q_aN0hmh6jgw' # Your Gemini API Key

# Configure the Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Global variables to store parsed FAQs and similar services
FAQS = {}
SIMILAR_SERVICES = {}

def parse_markdown_faqs(file_path: str) -> dict:
    faqs = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split content by questions, assuming questions start with '**' and end with '?'
    # This regex looks for '**Question?**\nAnswer'
    matches = re.findall(r'\*\*(.*?)\*\*\n(.*?)(?=\n\*\*|\Z)', content, re.DOTALL)

    for question, answer in matches:
        faqs[question.strip()] = answer.strip()
    return faqs

def parse_markdown_similar_services(file_path: str) -> dict:
    services = {}
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split content by service categories and then by individual services
    # This regex looks for '## Category\n\n* Service: Description'
    categories = re.split(r'##\s*(.*?)\n\n', content)[1:] # Skip the first empty split

    for i in range(0, len(categories), 2):
        category_name = categories[i].strip()
        category_content = categories[i+1].strip()
        
        # Extract individual services within the category
        service_matches = re.findall(r'\*\s*\*\*(.*?):\*\*\s*(.*?)(?=\n\*\*|\n\n|\Z)', category_content, re.DOTALL)
        
        services[category_name] = {name.strip(): desc.strip() for name, desc in service_matches}

    return services

def find_answer(query: str) -> str:
    query_lower = query.lower()

    # Search in FAQs first
    for question, answer in FAQS.items():
        if query_lower in question.lower():
            return f"FAQ: {answer}"
    
    # Search in Similar Services
    for category, services_dict in SIMILAR_SERVICES.items():
        if query_lower in category.lower():
            # If the query matches a category, return all services in that category
            response = f"Services in {category}:\n"
            for service_name, service_desc in services_dict.items():
                response += f"- {service_name}: {service_desc}\n"
            return response
        for service_name, service_desc in services_dict.items():
            if query_lower in service_name.lower() or query_lower in service_desc.lower():
                return f"Similar Service: {service_name} - {service_desc}"

    return "I'm sorry, I couldn't find an answer to that question in my knowledge base. Please try rephrasing your question or contact customer support at contact@jeffsservices.com."

async def get_gemini_response(prompt: str) -> str:
    """Gets a response from the Gemini model."""
    model = genai.GenerativeModel('gemini-pro')
    try:
        response = await model.generate_content_async(prompt)
        return response.text
    except Exception as e:
        print(f"Error getting response from Gemini: {e}")
        return "I'm sorry, I couldn't generate a response at this time. Please try again later."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        f"Hi {user.mention_html()}! I'm Jeff's Services Bot from JeffsServices.com. Ask me anything about our FAQs or similar services!",
    )

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answers user questions based on FAQs and similar services, or uses Gemini for general questions."""
    user_message = update.message.text
    answer = find_answer(user_message)

    # If no specific answer is found, try to get a response from Gemini
    if "I'm sorry, I couldn't find an answer" in answer:
        gemini_answer = await get_gemini_response(user_message)
        if gemini_answer:
            answer = gemini_answer

    await update.message.reply_text(answer)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /help is issued."""
    await update.message.reply_text("I can answer questions about Jeff's Services FAQs and similar services. Just ask me a question!")

def main() -> None:
    """Start the bot."""
    global FAQS, SIMILAR_SERVICES
    FAQS = parse_markdown_faqs('faqs.md')
    SIMILAR_SERVICES = parse_markdown_similar_services('similar_services.md')

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
