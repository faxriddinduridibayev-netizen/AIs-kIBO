import os
import io
import json
import asyncio
import pandas as pd
import pypdf
import docx
import psycopg2
from pgvector.psycopg2 import register_vector
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import google.generativeai as genai
import dropbox
from dropbox.exceptions import ApiError, AuthError
from datetime import datetime, timedelta
from jose import JWTError, jwt

app = FastAPI(title="Buxgalteriya Semantik Qidiruv Tizimi")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. KONFIGURATSIYA VA AUTH
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")

SECRET_KEY = os.getenv("SECRET_KEY", "buxgalteriya-maxfiy-kalit-12345")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# Buxgalterlar ro'yxati (Login: Parol)
USERS_DB = {
    "admin": "admin123",
    "buxgalter1": "pas2026"
}

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        register_vector(conn)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id BIGSERIAL PRIMARY KEY,
                dropbox_file_id VARCHAR(255) UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                file_link TEXT NOT NULL,
                extracted_text TEXT,
                embedding VECTOR(768),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        print("Baza va pgvector muvaffaqiyatli ulangan!")
    except Exception as e:
        print(f"Baza ulanishida xatolik: {e}")

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse("index.html")

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None or username not in USERS_DB:
            raise HTTPException(status_code=401, detail="Noma'lum foydalanuvchi")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Token yaroqsiz yoki muddati o'tgan")

# 2. FAYLLARDAN MATNNI AJRATISH
def extract_text_from_excel(file_bytes: bytes) -> str:
    extracted_text = ""
    try:
        excel_file = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
        for sheet_name, df in excel_file.items():
            extracted_text += f"\n--- Varaq: {sheet_name} ---\n"
            df_clean = df.fillna("")
            extracted_text += df_clean.to_string(index=False) + "\n"
    except Exception as e:
        print(f"Excel o'qishda xatolik: {e}")
    return extracted_text.strip()

def extract_text_from_pdf(file_bytes: bytes) -> str:
    text = ""
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        for page in reader.pages:
            text += page.extract_text() or ""
    except Exception as e:
        print(f"PDF o'qishda xatolik: {e}")
    return text.strip()

def extract_text_from_docx(file_bytes: bytes) -> str:
    try:
        doc = docx.Document(io.BytesIO(file_bytes))
        return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    except Exception as e:
        print(f"Word o'qishda xatolik: {e}")
        return ""

# 3. DROPBOX INDEKSALASH
def get_dropbox_client() -> dropbox.Dropbox:
    if not DROPBOX_ACCESS_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN topilmadi!")
    return dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)

def get_shared_link(dbx: dropbox.Dropbox, path: str) -> str:
    try:
        existing = dbx.sharing_list_shared_links(path=path, direct_only=True).links
        if existing:
            return existing[0].url
        created = dbx.sharing_create_shared_link_with_settings(path)
        return created.url
    except ApiError as e:
        print(f"{path} uchun havola olishda xatolik: {e}")
        return f"https://www.dropbox.com/home{path}"

def sync_dropbox_files(folder_path: str):
    try:
        dbx = get_dropbox_client()
    except RuntimeError as e:
        print(e)
        return

    try:
        result = dbx.files_list_folder(folder_path)
    except (ApiError, AuthError) as e:
        print(f"Dropbox papkasini o'qishda xatolik: {e}")
        return

    entries = list(result.entries)
    while result.has_more:
        result = dbx.files_list_folder_continue(result.cursor)
        entries.extend(result.entries)

    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)
    cur = conn.cursor()

    for entry in entries:
        if not isinstance(entry, dropbox.files.FileMetadata):
            continue

        file_id = entry.id
        file_name = entry.name
        file_path = entry.path_display
        lower_name = file_name.lower()

        if not lower_name.endswith(('.xlsx', '.xls', '.pdf', '.docx')):
            continue

        try:
            _, response = dbx.files_download(file_path)
            file_bytes = response.content
        except ApiError as e:
            print(f"{file_name} yuklab olishda xatolik: {e}")
            continue

        extracted_text = ""
        if lower_name.endswith(('.xlsx', '.xls')):
            extracted_text = extract_text_from_excel(file_bytes)
        elif lower_name.endswith('.pdf'):
            extracted_text = extract_text_from_pdf(file_bytes)
        elif lower_name.endswith('.docx'):
            extracted_text = extract_text_from_docx(file_bytes)

        if not extracted_text.strip():
            continue

        file_link = get_shared_link(dbx, file_path)

        embedding_res = genai.embed_content(
            model="models/text-embedding-004",
            content=extracted_text[:9000]
        )
        vector = embedding_res['embedding']

        cur.execute(
            """
            INSERT INTO documents (dropbox_file_id, file_name, file_link, extracted_text, embedding)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (dropbox_file_id)
            DO UPDATE SET
                file_name = EXCLUDED.file_name,
                file_link = EXCLUDED.file_link,
                extracted_text = EXCLUDED.extracted_text,
                embedding = EXCLUDED.embedding;
            """,
            (file_id, file_name, file_link, extracted_text, vector)
        )
        conn.commit()

    cur.close()
    conn.close()

# 4. ENDPOINTLAR
@app.post("/token")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user_password = USERS_DB.get(form_data.username)
    if not user_password or user_password != form_data.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login yoki parol noto'g'ri",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": form_data.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/sync")
def trigger_sync(folder_path: str, background_tasks: BackgroundTasks, current_user: str = Depends(get_current_user)):
    background_tasks.add_task(sync_dropbox_files, folder_path)
    return {"status": "ok", "message": "Indekslash orqa fonda boshlandi."}

@app.get("/search")
def search_documents(query: str = Query(...), current_user: str = Depends(get_current_user)):
    if not query.strip():
        raise HTTPException(status_code=400, detail="Qidiruv matni bo'sh bo'lmasligi kerak.")

    embedding_res = genai.embed_content(
        model="models/text-embedding-004",
        content=query
    )
    query_vector = embedding_res['embedding']

    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT file_name, file_link, extracted_text, 1 - (embedding <=> %s::vector) AS similarity
        FROM documents
        ORDER BY similarity DESC
        LIMIT 5;
        """,
        (query_vector,)
    )
    results = cur.fetchall()
    cur.close()
    conn.close()

    formatted_results = []
    for r in results:
        preview_text = r[2][:200] + "..." if r[2] else ""
        formatted_results.append({
            "file_name": r[0],
            "file_link": r[1],
            "preview": preview_text,
            "similarity_score": round(float(r[3]) * 100, 2)
        })

    return {"query": query, "results": formatted_results}
