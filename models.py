from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from database import Base
import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    area = Column(String, nullable=True)
    password = Column(String) # Stored as hashed
    is_active = Column(Boolean, default=True)

    complaints = relationship("Complaint", back_populates="reporter")

class Complaint(Base):
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(String, nullable=True)
    image_url = Column(String)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    status = Column(String, default="Pending")
    issue_type = Column(String, nullable=True) # This is subcategory select
    department = Column(String, nullable=True)
    voice_url = Column(String, nullable=True)
    severity_score = Column(Float, nullable=True)
    confidence_score = Column(Float, nullable=True)
    department_suggested = Column(String, nullable=True)
    votes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    reporter_id = Column(Integer, ForeignKey("users.id"))

    reporter = relationship("User", back_populates="complaints")

class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    department = Column(String)
    status = Column(String)
    phone = Column(String)
    location = Column(String)
    rating = Column(Float)
    active_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
