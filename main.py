from fastapi import FastAPI, Depends, HTTPException, status, File, UploadFile
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from database import engine, Base, SessionLocal
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import models, schemas, utils
import os
import uuid

load_dotenv()

# Supabase config from env
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "complaint-images")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="UrbanSathi Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, specify your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    from jose import JWTError, jwt
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, utils.SECRET_KEY, algorithms=[utils.ALGORITHM])
        phone: str = payload.get("sub")
        if phone is None:
            raise credentials_exception
        token_data = schemas.TokenData(phone_number=phone)
    except JWTError:
        raise credentials_exception
        
    user = db.query(models.User).filter(models.User.phone_number == token_data.phone_number).first()
    if user is None:
        raise credentials_exception
    return user



@app.post("/register", response_model=schemas.User)
async def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    try:
        print(f"Registering user: {user.phone_number}")
        db_user = db.query(models.User).filter(models.User.phone_number == user.phone_number).first()
        if db_user:
            print("User already exists")
            raise HTTPException(status_code=400, detail="Phone number already registered")
            
        hashed_password = utils.get_password_hash(user.password)
        new_user = models.User(
            phone_number=user.phone_number,
            password=hashed_password,
            name=user.name,
            area=user.area
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        print("User registered successfully")
        return new_user
    except Exception as e:
        print(f"Registration Error: {e}")
        import traceback
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/token", response_model=schemas.Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Using 'username' field for 'phone_number' because of OAuth2 spec
    user = db.query(models.User).filter(models.User.phone_number == form_data.username).first()
    if not user or not utils.verify_password(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = utils.create_access_token(data={"sub": user.phone_number})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/users/me", response_model=schemas.User)
def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user


# --- Supabase Storage Upload (Backend / service key only) ---
ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "audio/mpeg", "audio/mp4", "audio/wav", "audio/ogg", "audio/m4a", "audio/x-m4a",
}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

@app.post("/upload/", response_model=dict)
async def upload_file(file: UploadFile = File(...)):
    print(f"[UPLOAD] Received file: {file.filename}, type: {file.content_type}")

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise HTTPException(status_code=500, detail="Supabase storage not configured on server.")

    # Validate MIME type
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}. Allowed: {ALLOWED_MIME_TYPES}")

    file_bytes = await file.read()

    # Validate file size
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail=f"File too large. Max allowed: 5MB, received: {len(file_bytes) // 1024}KB")

    # Build unique path: public/<timestamp>_<uuid><ext>
    import time
    file_extension = os.path.splitext(file.filename or "file")[1] or ".jpg"
    timestamp = int(time.time() * 1000)
    unique_filename = f"public/{timestamp}_{uuid.uuid4().hex}{file_extension}"

    print(f"[UPLOAD] Uploading to Supabase bucket '{SUPABASE_BUCKET}', path: {unique_filename}")

    import httpx
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{unique_filename}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(upload_url, content=file_bytes, headers=headers)
        print(f"[UPLOAD] Supabase response: {res.status_code} — {res.text[:200]}")
        if res.status_code not in (200, 201):
            raise HTTPException(status_code=500, detail=f"Supabase upload failed ({res.status_code}): {res.text}")

    # Build permanent public URL
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{unique_filename}"
    print(f"[UPLOAD] ✅ Success! Public URL: {public_url}")
    return {"image_url": public_url}

def recalculate_priority(comp: models.Complaint, db: Session):
    # Removed the Severity Score weight factor (0.4)
    
    total_votes = (comp.yes_votes or 0) + (comp.no_votes or 0) + (comp.idk_votes or 0)
    ratio = float(comp.yes_votes or 0) / float(total_votes) if total_votes > 0 else 0.5
    comp.community_yes_ratio = ratio
    # Assigned both the previous 0.2 community + 0.4 AI weighting purely to the Community Validation Factor
    r_score = (ratio * 10) * 0.6
    
    try:
        matrix_entry = db.execute(
            text("SELECT urgency_index FROM department_urgency_matrix WHERE department = :dept AND issue_type = :type"),
            {"dept": comp.department, "type": comp.issue_type}
        ).fetchone()
        urgency = float(matrix_entry[0]) if matrix_entry else 0.5
    except Exception as e:
        urgency = 0.5
    comp.department_urgency_index = urgency
    u_score = (urgency * 10) * 0.2
    
    area_weight = 0.3
    if comp.latitude and comp.longitude:
        try:
            place = db.execute(
                text("SELECT weight FROM critical_places WHERE ST_Distance(location, ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography) <= 300 ORDER BY weight DESC LIMIT 1"),
                {"lng": comp.longitude, "lat": comp.latitude}
            ).fetchone()
            if place: area_weight = float(place[0])
        except Exception:
            pass
            
    comp.critical_area_weight = area_weight
    a_score = (area_weight * 10) * 0.2
    
    comp.priority_score = round(r_score + u_score + a_score, 2)


@app.post("/complaints/", response_model=schemas.ComplaintAIResponse)
def create_complaint(complaint: schemas.ComplaintCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    print(f"[COMPLAINT] Creating complaint for user: {current_user.phone_number}")
    print(f"[COMPLAINT] image_url: {complaint.image_url}")
    print(f"[COMPLAINT] voice_url: {complaint.voice_url}")
    print(f"[COMPLAINT] dept: {complaint.department}, sub: {complaint.subcategory}")
    print(f"[COMPLAINT] lat: {complaint.latitude}, lng: {complaint.longitude}")

    if not complaint.force_create and complaint.latitude and complaint.longitude:
        # Check for duplicates within 50 meters
        duplicate_query = text("""
            SELECT id FROM complaints 
            WHERE department = :dept 
            AND issue_type = :type 
            AND status IN ('Pending', 'In Progress')
            AND ST_Distance(
                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography, 
                ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
            ) <= 50 
            LIMIT 1
        """)
        try:
            dup = db.execute(duplicate_query, {
                "dept": complaint.department, 
                "type": complaint.subcategory, 
                "lng": complaint.longitude, 
                "lat": complaint.latitude
            }).fetchone()
            
            if dup:
                raise HTTPException(
                    status_code=409, 
                    detail={"message": "Similar issue exists", "existing_id": dup[0]}
                )
        except HTTPException:
            raise
        except Exception as e:
            print(f"[COMPLAINT] Duplicate check error (ignored): {e}")

    ai_severity = 7.5
    ai_confidence = 88.0
    ai_dept = "Roads & Bridges"

    try:
        new_complaint = models.Complaint(
            title=complaint.title,
            description=complaint.description,
            image_url=complaint.image_url,
            voice_url=complaint.voice_url,
            latitude=complaint.latitude,
            longitude=complaint.longitude,
            reporter_id=current_user.id,
            department=complaint.department,
            issue_type=complaint.subcategory,
            severity_score=ai_severity,
            confidence_score=ai_confidence,
            department_suggested=ai_dept
        )
        # We need to call recalculate_priority before saving to compute initial scores
        recalculate_priority(new_complaint, db)
        
        db.add(new_complaint)
        db.commit()
        db.refresh(new_complaint)
        print(f"[COMPLAINT] ✅ Complaint saved. ID: {new_complaint.id}, image_url: {new_complaint.image_url}, voice_url: {new_complaint.voice_url}")
        return new_complaint
    except Exception as e:
        print(f"[COMPLAINT] ❌ DB Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/complaints/", response_model=list[schemas.ComplaintAIResponse])
def get_all_complaints(db: Session = Depends(get_db)):
    complaints = db.query(models.Complaint).all()
    print(f"[FETCH] All complaints count: {len(complaints)}")
    for c in complaints:
        print(f"  - ID:{c.id} | image_url:{c.image_url} | voice_url:{c.voice_url}")
    return complaints

@app.get("/complaints/me", response_model=list[schemas.ComplaintAIResponse])
def get_my_complaints(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    complaints = db.query(models.Complaint).filter(models.Complaint.reporter_id == current_user.id).all()
    print(f"[FETCH] Complaints for user {current_user.phone_number}: {len(complaints)}")
    for c in complaints:
        print(f"  - ID:{c.id} | image_url:{c.image_url} | voice_url:{c.voice_url}")
    return complaints


@app.get("/workers/", response_model=list[schemas.Worker])
def get_all_workers(db: Session = Depends(get_db)):
    # If table empty, add mock ones
    workers = db.query(models.Worker).all()
    if not workers:
        mock_workers = [
            models.Worker(name="Rajinder Kumar", department="Roads & Bridges", status="Active", phone="+91 9876543100", location="Sector 14", rating=4.8),
            models.Worker(name="Suresh Patil", department="Waste Mgmt", status="On Leave", phone="+91 9876543101", location="N/A", rating=4.9),
            models.Worker(name="Amit Sharma", department="Water Supply", status="Assigned", phone="+91 9876543102", location="MG Road", rating=4.5),
        ]
        db.add_all(mock_workers)
        db.commit()
        workers = db.query(models.Worker).all()
    return workers

@app.post("/complaints/{complaint_id}/vote")
def cast_vote(complaint_id: int, vote: schemas.VoteCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    comp = db.query(models.Complaint).filter(models.Complaint.id == complaint_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Complaint not found")
        
    if comp.status in ["Resolved", "In Progress", "Rejected"]:
        raise HTTPException(status_code=400, detail="Voting is closed for this issue.")
        
    existing_vote = db.query(models.Vote).filter(
        models.Vote.user_id == current_user.id,
        models.Vote.complaint_id == complaint_id
    ).first()
    
    if existing_vote:
        raise HTTPException(status_code=400, detail="You have already voted on this issue.")
        
    if vote.vote_type not in ["Yes", "No", "Idk"]:
        raise HTTPException(status_code=400, detail="Invalid vote type. Must be Yes, No, or Idk.")
        
    new_vote = models.Vote(user_id=current_user.id, complaint_id=complaint_id, vote_type=vote.vote_type)
    db.add(new_vote)
    
    if comp.yes_votes is None: comp.yes_votes = 0
    if comp.no_votes is None: comp.no_votes = 0
    if comp.idk_votes is None: comp.idk_votes = 0
    
    if vote.vote_type == "Yes":
        comp.yes_votes += 1
    elif vote.vote_type == "No":
        comp.no_votes += 1
    elif vote.vote_type == "Idk":
        comp.idk_votes += 1
        
    comp.votes = (comp.yes_votes or 0) + (comp.no_votes or 0) + (comp.idk_votes or 0)
    recalculate_priority(comp, db)
        
    db.commit()
    db.refresh(comp)
    
    return {"status": "success", "yes_votes": comp.yes_votes, "no_votes": comp.no_votes, "idk_votes": comp.idk_votes}

@app.post("/complaints/{complaint_id}/feedback")
def submit_feedback(complaint_id: int, feedback_data: schemas.FeedbackCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    comp = db.query(models.Complaint).filter(models.Complaint.id == complaint_id, models.Complaint.reporter_id == current_user.id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Complaint not found or unauthorized.")
        
    if comp.status != "Resolved":
        raise HTTPException(status_code=400, detail="Feedback can only be provided for resolved issues.")
        
    comp.user_feedback = feedback_data.feedback
    comp.user_feedback_rating = feedback_data.rating
    db.commit()
    db.refresh(comp)
    
    return {"status": "success", "message": "Feedback submitted"}

@app.get("/my-votes")
def get_my_votes(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    user_votes = db.query(models.Vote.complaint_id).filter(models.Vote.user_id == current_user.id).all()
    voted_ids = [vote[0] for vote in user_votes]
    return {"voted_complaint_ids": voted_ids}

@app.patch("/complaints/{complaint_id}/status")
def update_complaint_status(complaint_id: int, status_update: dict, db: Session = Depends(get_db)):
    comp = db.query(models.Complaint).filter(models.Complaint.id == complaint_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Complaint not found")
    
    new_status = status_update.get("status")
    estimated_time = status_update.get("estimated_time")
    
    if new_status:
        comp.status = new_status
    if estimated_time:
        comp.estimated_completion_time = estimated_time
        
    db.commit()
    db.refresh(comp)
    return comp
