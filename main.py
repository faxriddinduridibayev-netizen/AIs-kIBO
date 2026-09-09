import os
import io
import dropbox
import pandas as pd
import pypdf
import docx
import psycopg2
from pgvector.psycopg2 import register_vector
from fastapi import FastAPI, Query, BackgroundTasks, HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from sentence_transformers import SentenceTransformer
from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

app = FastAPI(title="Dropbox Buxgalteriya Qidiruv Tizimi")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DROPBOX_ACCESS_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

SECRET_KEY = os.getenv("SECRET_KEY", "buxgalteriya-maxfiy-kalit-12345")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

USERS_DB = {
    "admin": "admin123",
    "buxgalter1": "pas2026"
}
@app.get("/")
def root():
    return RedirectResponse(url="/docs")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        register_vector(conn)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id BIGSERIAL PRIMARY KEY,
                dropbox_id VARCHAR(255) UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                file_link TEXT NOT NULL,
                extracted_text TEXT,
                embedding VECTOR(384),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Baza ulanishida xatolik: {e}")

@app.on_event("startup")
def startup_event():
    init_db()

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

def extract_text_from_excel(file_bytes: bytes) -> str:
    extracted_text = ""
    try:
        excel_file = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
        for sheet_name, df in excel_file.items():
            extracted_text += f"\n--- Varaq: {sheet_name} ---\n"
            extracted_text += df.fillna("").to_string(index=False) + "\n"
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

def sync_dropbox_folder(folder_path: str = ""):
    if not DROPBOX_ACCESS_TOKEN:
        print("DROPBOX_ACCESS_TOKEN topilmadi!")
        return

    dbx = dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
    if folder_path and not folder_path.startswith('/'):
        folder_path = '/' + folder_path

    res = dbx.files_list_folder(folder_path)

    conn = psycopg2.connect(DATABASE_URL)
    register_vector(conn)
    cur = conn.cursor()

    for entry in res.entries:
        if isinstance(entry, dropbox.files.FileMetadata):
            file_path = entry.path_lower
            file_name = entry.name
            
            _, response = dbx.files_download(file_path)
            file_bytes = response.content

            extracted_text = ""
            if file_name.endswith(('.xlsx', '.xls')):
                extracted_text = extract_text_from_excel(file_bytes)
            elif file_name.endswith('.pdf'):
                extracted_text = extract_text_from_pdf(file_bytes)
            elif file_name.endswith('.docx'):
                extracted_text = extract_text_from_docx(file_bytes)

            if not extracted_text.strip():
                continue

            try:
                shared_link = dbx.sharing_create_shared_link_with_settings(file_path).url
            except Exception:
                shared_link = dbx.sharing_list_shared_links(file_path).links[0].url

            vector = model.encode(extracted_text[:4000]).tolist()

            cur.execute(
                """
                INSERT INTO documents (dropbox_id, file_name, file_link, extracted_text, embedding)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (dropbox_id) 
                DO UPDATE SET 
                    file_name = EXCLUDED.file_name,
                    extracted_text = EXCLUDED.extracted_text,
                    embedding = EXCLUDED.embedding;
                """,
                (entry.id, file_name, shared_link, extracted_text, vector)
            )
            conn.commit()

    cur.close()
    conn.close()

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
def trigger_sync(folder_path: str = "", background_tasks: BackgroundTasks = None, current_user: str = Depends(get_current_user)):
    background_tasks.add_task(sync_dropbox_folder, folder_path)
    return {"status": "ok", "message": "Dropbox fayllarini indekslash orqa fonda boshlandi."}

@app.get("/search")
def search_documents(query: str = Query(...), current_user: str = Depends(get_current_user)):
    if not query.strip():
        raise HTTPException(status_code=400, detail="Qidiruv matni bo'sh bo'lmasligi kerak.")

    query_vector = model.encode(query).tolist()

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
