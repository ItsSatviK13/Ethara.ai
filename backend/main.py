from fastapi import FastAPI, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import List, Optional
from datetime import date, datetime
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import enum
import os
from contextlib import asynccontextmanager
import os 
from dotenv import load_dotenv
load_dotenv()

# ==============================
# MongoDB Config (USE ENV VARS)
# ==============================

MONGODB_URL = os.getenv("MONGODB_URL")
DATABASE_NAME = os.getenv("DATABASE_NAME", "hrms_lite")

if not MONGODB_URL:
    raise RuntimeError("MONGODB_URL environment variable not set")

mongodb_client: Optional[AsyncIOMotorClient] = None
database = None

# ==============================
# Enums
# ==============================

class AttendanceStatus(str, enum.Enum):
    PRESENT = "Present"
    ABSENT = "Absent"

# ==============================
# Models (Pydantic v2 style)
# ==============================

class EmployeeCreate(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=50)
    full_name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    department: str = Field(..., min_length=1, max_length=100)

    @field_validator("employee_id", "full_name", "department")
    @classmethod
    def no_empty_strings(cls, v):
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class Employee(BaseModel):
    id: str = Field(alias="_id")
    employee_id: str
    full_name: str
    email: str
    department: str

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class AttendanceCreate(BaseModel):
    employee_id: str
    date: date
    status: AttendanceStatus

    @field_validator("date")
    @classmethod
    def validate_date(cls, v):
        if v > date.today():
            raise ValueError("Attendance date cannot be in the future")
        return v


class Attendance(BaseModel):
    id: str = Field(alias="_id")
    employee_id: str
    date: date
    status: AttendanceStatus
    employee_name: Optional[str] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}


class AttendanceStats(BaseModel):
    employee_id: str
    employee_name: str
    total_present: int
    total_absent: int
    total_days: int


# ==============================
# Lifespan
# ==============================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global mongodb_client, database
    mongodb_client = AsyncIOMotorClient(MONGODB_URL)
    database = mongodb_client[DATABASE_NAME]

    try:
        await database.command("ping")
        await database.employees.create_index("employee_id", unique=True)
        await database.employees.create_index("email", unique=True)
        await database.attendance.create_index(
            [("employee_id", 1), ("date", 1)], unique=True
        )
        print("✅ Connected to MongoDB")
    except Exception as e:
        print("❌ MongoDB connection failed:", e)
        raise e

    yield
    mongodb_client.close()


# ==============================
# App
# ==============================

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# Helpers
# ==============================

def employee_helper(emp):
    emp["_id"] = str(emp["_id"])
    return emp

def attendance_helper(att):
    att["_id"] = str(att["_id"])
    att["date"] = att["date"].date()
    return att

# ==============================
# Routes
# ==============================

@app.get("/")
async def root():
    return {"status": "healthy"}

@app.post("/api/employees", response_model=Employee)
async def create_employee(employee: EmployeeCreate):
    if await database.employees.find_one({"employee_id": employee.employee_id}):
        raise HTTPException(400, "Employee ID already exists")

    if await database.employees.find_one({"email": employee.email}):
        raise HTTPException(400, "Email already exists")

    result = await database.employees.insert_one(employee.model_dump())
    new_emp = await database.employees.find_one({"_id": result.inserted_id})
    return employee_helper(new_emp)

@app.get("/api/employees", response_model=List[Employee])
async def get_employees():
    employees = []
    async for emp in database.employees.find():
        employees.append(employee_helper(emp))
    return employees


@app.post("/api/attendance", response_model=Attendance)
async def mark_attendance(attendance: AttendanceCreate):
    employee = await database.employees.find_one(
        {"employee_id": attendance.employee_id}
    )
    if not employee:
        raise HTTPException(404, "Employee not found")

    attendance_dict = attendance.model_dump()
    attendance_dict["date"] = datetime.combine(
        attendance.date, datetime.min.time()
    )
    attendance_dict["status"] = attendance.status.value

    existing = await database.attendance.find_one(
        {
            "employee_id": attendance.employee_id,
            "date": attendance_dict["date"],
        }
    )

    if existing:
        await database.attendance.update_one(
            {"_id": existing["_id"]},
            {"$set": {"status": attendance.status.value}},
        )
        updated = await database.attendance.find_one(
            {"_id": existing["_id"]}
        )
        updated["employee_name"] = employee["full_name"]
        return attendance_helper(updated)

    result = await database.attendance.insert_one(attendance_dict)
    new_att = await database.attendance.find_one(
        {"_id": result.inserted_id}
    )
    new_att["employee_name"] = employee["full_name"]
    return attendance_helper(new_att)


@app.get("/api/attendance", response_model=List[Attendance])
async def get_attendance(
    employee_id: Optional[str] = None,
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    query = {}

    if employee_id:
        query["employee_id"] = employee_id

    if date_from:
        date_from_obj = datetime.strptime(date_from, "%Y-%m-%d")
        query.setdefault("date", {})["$gte"] = date_from_obj

    if date_to:
        date_to_obj = datetime.strptime(date_to, "%Y-%m-%d")
        query.setdefault("date", {})["$lte"] = date_to_obj

    records = []
    async for record in database.attendance.find(query).sort("date", -1):
        emp = await database.employees.find_one(
            {"employee_id": record["employee_id"]}
        )
        record["employee_name"] = emp["full_name"] if emp else None
        records.append(attendance_helper(record))

    return records

@app.get("/api/attendance/stats", response_model=List[AttendanceStats])
async def get_attendance_stats():
    # Use aggregation pipeline for efficient stats calculation
    pipeline = [
        {
            "$group": {
                "_id": "$employee_id",
                "total_present": {
                    "$sum": {"$cond": [{"$eq": ["$status", "Present"]}, 1, 0]}
                },
                "total_absent": {
                    "$sum": {"$cond": [{"$eq": ["$status", "Absent"]}, 1, 0]}
                },
                "total_days": {"$sum": 1}
            }
        }
    ]
    
    # Get attendance stats
    attendance_stats = {}
    cursor = database.attendance.aggregate(pipeline)
    async for stat in cursor:
        attendance_stats[stat["_id"]] = {
            "total_present": stat["total_present"],
            "total_absent": stat["total_absent"],
            "total_days": stat["total_days"]
        }
    
    # Get all employees and combine with stats
    stats = []
    cursor = database.employees.find()
    async for employee in cursor:
        emp_id = employee["employee_id"]
        emp_stats = attendance_stats.get(emp_id, {
            "total_present": 0,
            "total_absent": 0,
            "total_days": 0
        })
        
        stats.append(AttendanceStats(
            employee_id=emp_id,
            employee_name=employee["full_name"],
            total_present=emp_stats["total_present"],
            total_absent=emp_stats["total_absent"],
            total_days=emp_stats["total_days"]
        ))
    
    return stats

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)