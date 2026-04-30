"""Telegram bot for ActiveMemory.

Provides commands: /search, /context, /list, /add, /stats.
"""

import logging

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler,
)

from ..core.config import config
from ..search.searcher import HybridSearcher
from ..storage.db import get_session, Document

logger = logging.getLogger(__name__)

# Conversation states
CHOOSING, TYPING = range(2)

# Initialize searcher
searcher = HybridSearcher()


def _check_access(user_id: int) -> bool:
    """Check if user has access to the bot."""
    admin_ids = [int(x.strip()) for x in config.telegram.admin_ids.split(",") if x.strip()]
    allowed_users = [int(x.strip()) for x in config.telegram.allowed_users.split(",") if x.strip()]
    
    if user_id in admin_ids:
        return True
    if allowed_users and user_id in allowed_users:
        return True
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    if not _check_access(user.id):
        await update.message.reply_text("⛔️ Доступ запрещён.")
        return
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        f"🧠 ActiveMemory Bot\n\n"
        f"Доступные команды:\n"
        f"/search <запрос> — поиск по памяти\n"
        f"/context <запрос> — умный контекст\n"
        f"/list — список документов\n"
        f"/stats — статистика\n"
        f"/help — справка"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    if not _check_access(update.effective_user.id):
        return
    
    await update.message.reply_text(
        "📖 Справка по командам:\n\n"
        "/search <запрос> — гибридный поиск (вектор + текст)\n"
        "   Пример: /search Python tutorial\n\n"
        "/context <запрос> — контекст с лимитом токенов\n"
        "   Пример: /context как работает FastAPI\n\n"
        "/list [limit] — список документов\n"
        "   Пример: /list 10\n\n"
        "/stats — статистика памяти\n"
        "/add — добавить документ (ответьте файлом)\n"
        "/help — эта справка"
    )


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command."""
    if not _check_access(update.effective_user.id):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("❌ Укажите поисковый запрос.\nПример: /search Python")
        return
    
    await update.message.reply_text("🔍 Ищу...")
    
    try:
        results = searcher.search(query, top_k=5)
        
        if not results:
            await update.message.reply_text("😔 Ничего не найдено.")
            return
        
        lines = [f"📊 Найдено результатов: {len(results)}\n"]
        for i, r in enumerate(results[:5], 1):
            score = r.score
            lines.append(f"{i}. {r.content[:200]}...")
            lines.append(f"   📍 Score: {score:.3f} | Source: {r.source}")
            lines.append("")
        
        await update.message.reply_text("\n".join(lines))
    
    except Exception as e:
        logger.error(f"Search error: {e}")
        await update.message.reply_text(f"❌ Ошибка поиска: {e}")


async def context_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /context command."""
    if not _check_access(update.effective_user.id):
        return
    
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("❌ Укажите запрос.\nПример: /context FastAPI middleware")
        return
    
    await update.message.reply_text("🧠 Собираю контекст...")
    
    try:
        results = searcher.smart_context(query, max_tokens=2000)
        
        if not results:
            await update.message.reply_text("😔 Контекст не найден.")
            return
        
        lines = ["📚 Контекст:\n"]
        total_tokens = sum(r.metadata.get("token_count", 0) for r in results)
        
        for r in results:
            lines.append(f"--- {r.source} (score: {r.score:.3f}) ---")
            lines.append(r.content[:500])
            lines.append("")
        
        lines.append(f"\n📊 Всего токенов: {total_tokens}")
        
        # Telegram has 4096 char limit
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:4000] + "\n... (обрезано)"
        
        await update.message.reply_text(text)
    
    except Exception as e:
        logger.error(f"Context error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command."""
    if not _check_access(update.effective_user.id):
        return
    
    limit = 10
    if context.args:
        try:
            limit = int(context.args[0])
        except ValueError:
            pass
    
    session = get_session()
    try:
        docs = session.query(Document).order_by(Document.created_at.desc()).limit(limit).all()
        
        if not docs:
            await update.message.reply_text("📂 База пуста.")
            return
        
        lines = [f"📚 Документы (последние {len(docs)}):\n"]
        for doc in docs:
            lines.append(f"• {doc.filename} ({doc.filetype}) — {len(doc.chunks)} чанков")
        
        await update.message.reply_text("\n".join(lines))
    
    except Exception as e:
        logger.error(f"List error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        session.close()


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    if not _check_access(update.effective_user.id):
        return
    
    session = get_session()
    try:
        from sqlalchemy import func
        
        doc_count = session.query(func.count(Document.id)).scalar()
        chunk_count = session.query(func.count()).select_from(Document).join(Document.chunks).scalar()
        
        await update.message.reply_text(
            "📊 Статистика ActiveMemory:\n\n"
            f"📄 Документов: {doc_count}\n"
            f"📋 Чанков: {chunk_count}\n"
        )
    
    except Exception as e:
        logger.error(f"Stats error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
    finally:
        session.close()


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command — start conversation."""
    if not _check_access(update.effective_user.id):
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📎 Отправьте файл для добавления в память.\n"
        "Поддерживаемые форматы: .txt, .pdf, .md, .py, .js, ...\n"
        "Или /cancel для отмены."
    )
    return CHOOSING


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload."""
    if not update.message.document:
        await update.message.reply_text("❌ Отправте файл.")
        return CHOOSING
    
    await update.message.reply_text("📥 Загружаю файл...")
    
    try:
        file = await update.message.document.get_file()
        file_path = f"/tmp/telegram_{update.message.document.file_name}"
        await file.download_to_drive(file_path)
        
        # Process file
        from ..ingest.processor import DocumentProcessor
        processor = DocumentProcessor()
        result = processor.ingest_document(file_path)
        
        if result["success"]:
            await update.message.reply_text(
                f"✅ Документ добавлен!\n"
                f"📄 {result['filename']}\n"
                f"📋 Чанков: {result['chunks']}"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка: {result.get('message', 'Unknown')}")
        
        # Cleanup
        import os
        if os.path.exists(file_path):
            os.remove(file_path)
    
    except Exception as e:
        logger.error(f"Add file error: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation."""
    await update.message.reply_text("❌ Отменено.")
    return ConversationHandler.END


def main():
    """Start the Telegram bot."""
    if not config.telegram.bot_token:
        logger.error("TG_BOT_TOKEN not set!")
        return
    
    app = Application.builder().token(config.telegram.bot_token).build()
    
    # Conversation handler for /add
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            CHOOSING: [MessageHandler(filters.Document.ALL, handle_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("context", context_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(conv_handler)
    
    logger.info("Telegram bot started...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
