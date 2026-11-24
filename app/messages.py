"""User-facing error messages shared by the API and the background indexer."""

BUSY_MESSAGE = "The AI service is overloaded right now. Please try again in a minute."
QUOTA_MESSAGE = (
    "The AI service's usage limit was reached (too many requests too quickly, or today's free quota "
    "is used up). Wait a minute and try again. If it keeps happening, check your Gemini plan limits."
)
def saved_progress_note(done: int, total: int) -> str:
    """Reassurance appended to an indexing failure when part of the file did get saved."""
    if 0 < done < total:
        return (
            f" Your progress is saved ({done:,} of {total:,} sections): add the same file again "
            "to continue where it stopped."
        )
    return ""


def daily_quota_message(done: int, total: int) -> str:
    return (
        "Today's free Gemini limit for indexing documents has been reached. It resets daily (around "
        "midnight Pacific time), or you can use a paid Gemini plan." + saved_progress_note(done, total)
    )


MODEL_FAILED_MESSAGE = "The language model request failed. Check the server logs."
NO_TEXT_MESSAGE = "No text could be read from that PDF. Scanned pages (pictures of text) aren't supported."
UNREADABLE_PDF_MESSAGE = "That PDF couldn't be read. It may be damaged or password-protected."
