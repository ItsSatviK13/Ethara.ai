from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional
from datetime import date, datetime
from sqlalchemy import create_engine, Column, Integer, String, Date, Enum as SQLEnum, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from contextlib import contextmanager
import enum
import os

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hrms.db")
# Fix for render.com postgres URL
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Enums
class AttendanceStatus(str, enum.Enum):
    PRESENT = "Present"
    ABSENT = "Absent"

# Database Models
class EmployeeDB(Base):
    __tablename__ = "employees"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    department = Column(String, nullable=False)
    
    attendance_records = relationship("AttendanceDB", back_populates="employee", cascade="all, delete-orphan")

class AttendanceDB(Base):
    __tablename__ = "attendance"
    
    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(String, ForeignKey("employees.employee_id", ondelete="CASCADE"), nullable=False)
    date = Column(Date, nullable=False)
    status = Column(SQLEnum(AttendanceStatus), nullable=False)
    
    employee = relationship("EmployeeDB", back_populates="attendance_records")

# Create tables
Base.metadata.create_all(bind=engine)

# Pydantic Models
class EmployeeCreate(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=50, description="Unique employee identifier")
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    department: str = Field(..., min_length=1, max_length=100)
    
    @validator('employee_id', 'full_name', 'department')
    def no_empty_strings(cls, v):
        if not v or not v.strip():
            raise ValueError('Field cannot be empty or whitespace')
        return v.strip()

class Employee(BaseModel):
    id: int
    employee_id: str
    full_name: str
    email: str
    department: str
    
    class Config:
        from_attributes = True

class AttendanceCreate(BaseModel):
    employee_id: str = Field(..., description="Employee ID to mark attendance for")
    date: date
    status: AttendanceStatus
    
    @validator('date')
    def validate_date(cls, v):
        if v > date.today():
            raise ValueError('Attendance date cannot be in the future')
        return v

class Attendance(BaseModel):
    id: int
    employee_id: str
    date: date
    status: AttendanceStatus
    employee_name: Optional[str] = None
    
    class Config:
        from_attributes = True

class AttendanceStats(BaseModel):
    employee_id: str
    employee_name: str
    total_present: int
    total_absent: int
    total_days: int

# FastAPI app
app = FastAPI(title="HRMS Lite API", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database dependency
@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Health check
@app.get("/")
def read_root():
    return {"status": "healthy", "message": "HRMS Lite API is running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

# Employee endpoints
@app.post("/api/employees", response_model=Employee, status_code=status.HTTP_201_CREATED)
def create_employee(employee: EmployeeCreate):
    with get_db() as db:
        # Check if employee_id already exists
        existing = db.query(EmployeeDB).filter(EmployeeDB.employee_id == employee.employee_id).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Employee ID '{employee.employee_id}' already exists"
            )
        
        # Check if email already exists
        existing_email = db.query(EmployeeDB).filter(EmployeeDB.email == employee.email).first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Email '{employee.email}' is already registered"
            )
        
        db_employee = EmployeeDB(**employee.dict())
        db.add(db_employee)
        db.commit()
        db.refresh(db_employee)
        return db_employee

@app.get("/api/employees", response_model=List[Employee])
def get_employees():
    with get_db() as db:
        employees = db.query(EmployeeDB).all()
        return employees

@app.get("/api/employees/{employee_id}", response_model=Employee)
def get_employee(employee_id: str):
    with get_db() as db:
        employee = db.query(EmployeeDB).filter(EmployeeDB.employee_id == employee_id).first()
        if not employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee with ID '{employee_id}' not found"
            )
        return employee

@app.delete("/api/employees/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(employee_id: str):
    with get_db() as db:
        employee = db.query(EmployeeDB).filter(EmployeeDB.employee_id == employee_id).first()
        if not employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee with ID '{employee_id}' not found"
            )
        
        db.delete(employee)
        db.commit()
        return None

# Attendance endpoints
@app.post("/api/attendance", response_model=Attendance, status_code=status.HTTP_201_CREATED)
def mark_attendance(attendance: AttendanceCreate):
    with get_db() as db:
        # Check if employee exists
        employee = db.query(EmployeeDB).filter(EmployeeDB.employee_id == attendance.employee_id).first()
        if not employee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Employee with ID '{attendance.employee_id}' not found"
            )
        
        # Check if attendance already marked for this date
        existing = db.query(AttendanceDB).filter(
            AttendanceDB.employee_id == attendance.employee_id,
            AttendanceDB.date == attendance.date
        ).first()
        
        if existing:
            # Update existing record
            existing.status = attendance.status
            db.commit()
            db.refresh(existing)
            result = Attendance.from_orm(existing)
            result.employee_name = employee.full_name
            return result
        
        # Create new attendance record
        db_attendance = AttendanceDB(**attendance.dict())
        db.add(db_attendance)
        db.commit()
        db.refresh(db_attendance)
        
        result = Attendance.from_orm(db_attendance)
        result.employee_name = employee.full_name
        return result

@app.get("/api/attendance", response_model=List[Attendance])
def get_attendance(employee_id: Optional[str] = None, date_from: Optional[date] = None, date_to: Optional[date] = None):
    with get_db() as db:
        query = db.query(AttendanceDB).join(EmployeeDB)
        
        if employee_id:
            query = query.filter(AttendanceDB.employee_id == employee_id)
        
        if date_from:
            query = query.filter(AttendanceDB.date >= date_from)
        
        if date_to:
            query = query.filter(AttendanceDB.date <= date_to)
        
        query = query.order_by(AttendanceDB.date.desc())
        records = query.all()
        
        result = []
        for record in records:
            att = Attendance.from_orm(record)
            att.employee_name = record.employee.full_name
            result.append(att)
        
        return result

@app.get("/api/attendance/stats", response_model=List[AttendanceStats])
def get_attendance_stats():
    with get_db() as db:
        employees = db.query(EmployeeDB).all()
        stats = []
        
        for emp in employees:
            total_present = db.query(AttendanceDB).filter(
                AttendanceDB.employee_id == emp.employee_id,
                AttendanceDB.status == AttendanceStatus.PRESENT
            ).count()
            
            total_absent = db.query(AttendanceDB).filter(
                AttendanceDB.employee_id == emp.employee_id,
                AttendanceDB.status == AttendanceStatus.ABSENT
            ).count()
            
            stats.append(AttendanceStats(
                employee_id=emp.employee_id,
                employee_name=emp.full_name,
                total_present=total_present,
                total_absent=total_absent,
                total_days=total_present + total_absent
            ))
        
        return stats

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)