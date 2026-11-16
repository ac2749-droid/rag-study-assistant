"""Step 4: a FastAPI app with one endpoint: POST /ask -> answer grounded in the stored chunks."""
import time

from fastapi import FastAPI, HTTPException
from google.genai.errors import ServerError
from pydantic import BaseModel

from embed_store import embed_texts, get_client, get_collection

CHAT_MODEL = "gemini-3.5-flash"
TOP_K = 4  # how many chunks to retrieve per question
# Cosine distance cutoff (lower = closer). On the sample deck, on-topic questions had their
# best chunk at 0.20-0.25 and off-topic ones never got closer than 0.43, so 0.38 sits in the gap.
MAX_DISTANCE = 0.38

app = FastAPI(title="NJIT Study Assistant")
client = get_client()
collection = get_collection()


class Question(BaseModel):
    question: str


PROMPT = """You are a study assistant. Answer the student's question using ONLY the lecture \
excerpts below. If the excerpts do not contain the answer, say you can't find it in the \
material instead of guessing. Be clear and concise.

EXCERPTS:
{context}

QUESTION: {question}"""


@app.post("/ask")
def ask(body: Question):
    q = body.question.strip()
    if not q:
        raise HTTPException(400, "Question is empty.")
    if collection.count() == 0:
        raise HTTPException(409, "No material stored yet. Run embed_store.py first.")

    # 1. embed the question, 2. find the closest chunks
    q_vec = embed_texts(client, [q], "RETRIEVAL_QUERY")[0]
    hits = collection.query(query_embeddings=[q_vec], n_results=TOP_K)
    # Keep only chunks that are actually close in meaning to the question.
    kept = [
        (d, m)
        for d, m, dist in zip(hits["documents"][0], hits["metadatas"][0], hits["distances"][0])
        if dist <= MAX_DISTANCE
    ]
    if not kept:  # nothing relevant: don't bother Gemini, and don't list unrelated pages
        return {"answer": "I can't find that in the uploaded material.", "sources": []}
    docs, metas = [d for d, _ in kept], [m for _, m in kept]

    # 3. give those chunks to Gemini along with the question
    context = "\n\n".join(f"[Page {m['page']}]\n{d}" for d, m in zip(docs, metas))
    prompt = PROMPT.format(context=context, question=q)
    for attempt in range(4):  # Gemini sometimes returns a temporary 503 "high demand"
        try:
            resp = client.models.generate_content(model=CHAT_MODEL, contents=prompt)
            break
        except ServerError:
            if attempt == 3:
                raise HTTPException(503, "Gemini is busy right now. Try again in a minute.")
            time.sleep(2 * (attempt + 1))

    # 4. return the answer plus where it came from
    return {
        "answer": resp.text,
        "sources": [  # one entry per page, in retrieval order
            {"page": p, "source": s}
            for p, s in dict.fromkeys((m["page"], m["source"]) for m in metas)
        ],
    }
