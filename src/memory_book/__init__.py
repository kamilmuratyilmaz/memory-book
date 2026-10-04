def main() -> None:
    """Run the Memory Book server (API + built web app)."""
    import os

    import uvicorn
    from dotenv import find_dotenv, load_dotenv

    # .env from the working directory (or a parent), like docker compose; real environment variables win
    load_dotenv(find_dotenv(usecwd=True))
    uvicorn.run("memory_book.app:create_app", factory=True, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))
