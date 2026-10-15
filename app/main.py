"""Employee Attendance & Analytics API (FastAPI + MongoDB).
Implements the HROne v2.0 contract. All instants are epoch milliseconds at the API boundary.
"""
import os, re, calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from bson import json_util

load_dotenv()
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tz_aware=True, serverSelectionTimeoutMS=3000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}


def ensure_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)], name="department_employee")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    db.attendance_logs.create_index([("status", ASCENDING), ("date", ASCENDING)], name="attendance_status_date")
    db.attendance_logs.create_index([("date", ASCENDING), ("late_minutes", DESCENDING), ("emp_code", ASCENDING)], name="attendance_late_date")

@app.on_event("startup")
def startup():
    ensure_indexes()


def dt_from_ms(value: int) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 100_000_000_000 or value > 4_102_444_800_000:
        raise HTTPException(422, "timestamp must be an epoch-millisecond integer in the supported range")
    return datetime.fromtimestamp(value / 1000, UTC).replace(microsecond=0)

def ms_from_dt(value):
    if value is None: return None
    if value.tzinfo is None: value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)

def now_utc(): return datetime.now(UTC).replace(microsecond=0)
def round_half_up(n, places=2):
    return float(Decimal(str(n)).quantize(Decimal('1').scaleb(-places), rounding=ROUND_HALF_UP))
def valid_date(s):
    try: return date.fromisoformat(s)
    except Exception: raise HTTPException(422, "date must be YYYY-MM-DD")
def valid_month(s):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s): raise HTTPException(422, "month must be YYYY-MM")
    y,m=map(int,s.split('-')); return date(y,m,1), date(y,m,calendar.monthrange(y,m)[1])
def iso_ms_to_ist_date(dt): return dt.astimezone(IST).date()
def shift_dt(day: date, hhmm: str):
    h,m=map(int,hhmm.split(':')); return datetime.combine(day,time(h,m),IST)
def attendance_day(punch_in, emp):
    local=punch_in.astimezone(IST); d=local.date()
    if emp['shift_end'] <= emp['shift_start'] and local.strftime('%H:%M') < emp['shift_end']:
        d -= timedelta(days=1)
    return d.isoformat()
def late_minutes(punch_in, emp):
    local=punch_in.astimezone(IST); day=local.date()
    if emp['shift_end'] <= emp['shift_start'] and local.strftime('%H:%M') < emp['shift_end']:
        day -= timedelta(days=1)
    start=shift_dt(day,emp['shift_start']).astimezone(UTC)
    delta=(punch_in-start).total_seconds()
    return int(delta//60) if delta > 600 else 0
def work_hours(pin,pout): return round_half_up((pout-pin).total_seconds()/3600,2)
def overtime_minutes(pout, emp, daystr):
    day=valid_date(daystr); end_day=day
    if emp['shift_end'] <= emp['shift_start']: end_day += timedelta(days=1)
    end=shift_dt(end_day,emp['shift_end']).astimezone(UTC)
    mins=int((pout-end).total_seconds()//60)
    return mins if mins >= 30 else 0
def record_out(d):
    return {k:v for k,v in d.items() if k != '_id'} | {
        'punch_in':ms_from_dt(d.get('punch_in')), 'punch_out':ms_from_dt(d.get('punch_out')),
        'history':[{'at':ms_from_dt(h['at']), 'by':h['by'], 'reason':h['reason'], 'changes':{
            k:{'from':ms_from_dt(v.get('from')) if k in ('punch_in','punch_out') else v.get('from'),
               'to':ms_from_dt(v.get('to')) if k in ('punch_in','punch_out') else v.get('to')} for k,v in h.get('changes',{}).items()}}
            for h in d.get('history',[])],
    }
def employee_out(d): return {k:v for k,v in d.items() if k!='_id'} | {'created_at':ms_from_dt(d['created_at'])}
def page_params(page,page_size):
    if page < 1 or page_size < 1 or page_size > 100: raise HTTPException(422,"page must be >=1 and page_size must be 1..100")
    return (page-1)*page_size

class EmployeeCreate(BaseModel):
    model_config=ConfigDict(extra='ignore')
    emp_code:str=Field(pattern=r'^EMP\d{4,6}$'); name:str=Field(min_length=1,max_length=100)
    email:str=Field(pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$',max_length=120); department:str=Field(min_length=1,max_length=50)
    shift_start:str='09:30'; shift_end:str='18:30'; joined_on:str
    @field_validator('shift_start','shift_end')
    @classmethod
    def shift_format(cls,v):
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',v): raise ValueError('must be HH:MM')
        return v
    @field_validator('joined_on')
    @classmethod
    def date_format(cls,v): date.fromisoformat(v); return v
    @model_validator(mode='after')
    def different_shift(self):
        if self.shift_start==self.shift_end: raise ValueError('shift_start must differ from shift_end')
        return self
class PunchInRequest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    emp_code:str; punched_at:Optional[int]=None; status:Literal['PRESENT','WFH','ON_DUTY']='PRESENT'
    @field_validator('punched_at')
    @classmethod
    def ts(cls,v):
        if v is not None: dt_from_ms(v)
        return v
class PunchOutRequest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    emp_code:str; punched_at:Optional[int]=None
    @field_validator('punched_at')
    @classmethod
    def ts(cls,v):
        if v is not None: dt_from_ms(v)
        return v
class RegularizeRequest(BaseModel):
    model_config=ConfigDict(extra='ignore')
    status:Optional[Literal['PRESENT','WFH','ON_DUTY','ABSENT','LEAVE']]=None
    punch_in:Optional[int]=None; punch_out:Optional[int]=None
    reason:str=Field(min_length=5,max_length=200); regularized_by:str=Field(min_length=1,max_length=50)
    @field_validator('punch_in','punch_out')
    @classmethod
    def ts(cls,v):
        if v is not None: dt_from_ms(v)
        return v

@app.get('/health')
def health():
    try: client.admin.command('ping'); return {'status':'ok'}
    except Exception: raise HTTPException(503,'MongoDB unavailable')

@app.post('/employees',status_code=201)
def create_employee(body:EmployeeCreate):
    doc=body.model_dump(); doc['created_at']=now_utc()
    try: db.employees.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'emp_code already exists')
    return employee_out(doc)

@app.get('/employees')
def list_employees(department:Optional[str]=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    q={'department':department} if department is not None else {}; skip=page_params(page,page_size)
    total=db.employees.count_documents(q)
    items=[employee_out(x) for x in db.employees.find(q,{'_id':0}).sort('emp_code',ASCENDING).skip(skip).limit(page_size)]
    return {'items':items,'total':total,'page':page,'page_size':page_size}

@app.post('/attendance/punch-in',status_code=201)
def punch_in(body:PunchInRequest):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    pin=dt_from_ms(body.punched_at) if body.punched_at is not None else now_utc()
    d=attendance_day(pin,emp)
    doc={'emp_code':body.emp_code,'date':d,'status':body.status,'punch_in':pin,'punch_out':None,'work_hours':None,
         'late_minutes':late_minutes(pin,emp),'overtime_minutes':0,'half_day':False,'history':[]}
    try: db.attendance_logs.insert_one(doc)
    except DuplicateKeyError: raise HTTPException(409,'already punched in for this date')
    return record_out(doc)

@app.post('/attendance/punch-out')
def punch_out(body:PunchOutRequest):
    emp=db.employees.find_one({'emp_code':body.emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    pout=dt_from_ms(body.punched_at) if body.punched_at is not None else now_utc()
    rec=db.attendance_logs.find_one({'emp_code':body.emp_code,'punch_in':{'$lte':pout}},sort=[('punch_in',DESCENDING)])
    if not rec: raise HTTPException(404,'no punch-in found')
    if rec.get('punch_out') is not None: raise HTTPException(409,'already punched out')
    pin=rec['punch_in']
    if pout<=pin or pout-pin>timedelta(hours=24): raise HTTPException(422,'punch-out must be after punch-in and within 24 hours')
    wh=work_hours(pin,pout); ot=overtime_minutes(pout,emp,rec['date'])
    updated=db.attendance_logs.find_one_and_update({'emp_code':body.emp_code,'date':rec['date'],'punch_out':None},
       {'$set':{'punch_out':pout,'work_hours':wh,'overtime_minutes':ot,'half_day':wh<4.50}},return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409,'already punched out')
    return record_out(updated)

@app.get('/attendance')
def list_attendance(emp_code:Optional[str]=None,date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,
                    page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    skip=page_params(page,page_size); q={}
    if emp_code: q['emp_code']=emp_code
    if date_from or date_to:
        q['date']={}
        if date_from: valid_date(date_from); q['date']['$gte']=date_from
        if date_to: valid_date(date_to); q['date']['$lte']=date_to
        if date_from and date_to and date_from>date_to: raise HTTPException(422,'date_from must be <= date_to')
    if status:
        if status not in STATUSES: raise HTTPException(422,'invalid status')
        q['status']=status
    total=db.attendance_logs.count_documents(q)
    items=[record_out(x) for x in db.attendance_logs.find(q,{'_id':0}).sort([('date',DESCENDING),('emp_code',ASCENDING)]).skip(skip).limit(page_size)]
    return {'items':items,'total':total,'page':page,'page_size':page_size}

@app.patch('/attendance/{emp_code}/{date}')
def regularize(emp_code:str,date:str,body:RegularizeRequest):
    date_str=date
    valid_date(date_str)
    emp=db.employees.find_one({'emp_code':emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    old=db.attendance_logs.find_one({'emp_code':emp_code,'date':date_str})
    if not old: raise HTTPException(404,'attendance record not found')
    new={k:old.get(k) for k in ('status','punch_in','punch_out','work_hours','late_minutes','overtime_minutes','half_day')}
    supplied=body.model_dump(exclude_unset=True)
    if supplied.get('status') is not None: new['status']=supplied['status']
    for f in ('punch_in','punch_out'):
        if f in supplied: new[f]=dt_from_ms(supplied[f]) if supplied[f] is not None else None
    if new['status'] in ('ABSENT','LEAVE'):
        if 'punch_in' in supplied or 'punch_out' in supplied: raise HTTPException(422,'ABSENT/LEAVE cannot include punch times')
        new['punch_in']=new['punch_out']=None
    else:
        if new['punch_in'] is None: raise HTTPException(422,'presence status requires punch_in')
        if attendance_day(new['punch_in'],emp)!=date_str: raise HTTPException(422,'punch_in must belong to attendance date')
        if new['punch_out'] is not None and (new['punch_out']<=new['punch_in'] or new['punch_out']-new['punch_in']>timedelta(hours=24)):
            raise HTTPException(422,'punch_out must be after punch_in and within 24 hours')
    if new['status'] in ('ABSENT','LEAVE'):
        new.update(work_hours=None,late_minutes=0,overtime_minutes=0,half_day=False)
    else:
        new['late_minutes']=late_minutes(new['punch_in'],emp)
        if new['punch_out'] is None: new.update(work_hours=None,overtime_minutes=0,half_day=False)
        else:
            new['work_hours']=work_hours(new['punch_in'],new['punch_out']); new['overtime_minutes']=overtime_minutes(new['punch_out'],emp,date_str); new['half_day']=new['work_hours']<4.5
    fields=('status','punch_in','punch_out','work_hours','late_minutes','overtime_minutes','half_day')
    changes={f:{'from':old.get(f),'to':new.get(f)} for f in fields if old.get(f)!=new.get(f)}
    if not changes: raise HTTPException(422,'request does not change the record')
    entry={'at':now_utc(),'by':body.regularized_by,'reason':body.reason,'changes':changes}
    # Compare-and-swap guards against overwriting another correction; history append is atomic.
    query={'emp_code':emp_code,'date':date_str,'history':old.get('history',[])}
    for f in fields: query[f]=old.get(f)
    update={'$set':{f:new[f] for f in fields},'$push':{'history':entry}}
    updated=db.attendance_logs.find_one_and_update(query,update,return_document=ReturnDocument.AFTER)
    if not updated: raise HTTPException(409,'record changed concurrently; retry')
    return record_out(updated)

# Analytics are MongoDB aggregation pipelines, not Python document loops.
def month_match(month):
    start,end=valid_month(month); return {'date':{'$gte':start.isoformat(),'$lte':end.isoformat()}},start,end
def weekday_count(start,end):
    n=0; d=start
    while d<=end:
        if d.weekday()<5: n+=1
        d+=timedelta(days=1)
    return n
def present_expr(): return {'$cond':[{'$in':['$status',list(PRESENCE)]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}

@app.get('/analytics/employees/{emp_code}/monthly')
def employee_monthly(emp_code:str,month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$')):
    emp=db.employees.find_one({'emp_code':emp_code})
    if not emp: raise HTTPException(404,'unknown employee')
    match, start, end = month_match(month)

    joined_on = date.fromisoformat(emp['joined_on'])
    start = max(start, joined_on)

    # Exclude attendance records before the employee's joining date.
    match['date']['$gte'] = start.isoformat()

    working = weekday_count(start, end) if start <= end else 0
    pipe=[{'$match':{'emp_code':emp_code,**match}},{'$group':{'_id':None,'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$status',list(PRESENCE)]},{'$lt':[{'$isoDayOfWeek':{'$dateFromString':{'dateString':'$date'}}},6]}]},{'$cond':[{'$eq':['$half_day',True]},0.5,1]},0]}},'leave_days':{'$sum':{'$cond':[{'$eq':['$status','LEAVE']},1,0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'total_overtime_minutes':{'$sum':{'$ifNull':['$overtime_minutes',0]}}}}]
    r=list(db.attendance_logs.aggregate(pipe)); x=r[0] if r else {}
    present=round_half_up(x.get('present_days',0)); pct=round_half_up(present/working*100,2) if working else None
    return {'emp_code':emp_code,'month':month,'working_days':working,'present_days':present,'leave_days':x.get('leave_days',0),'late_count':x.get('late_count',0),'total_late_minutes':x.get('total_late_minutes',0),'total_overtime_minutes':x.get('total_overtime_minutes',0),'attendance_pct':pct}

@app.get('/analytics/departments/summary')
def department_summary(month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),department:Optional[str]=None):
    match,start,end=month_match(month); empmatch={'joined_on':{'$lte':end.isoformat()}}
    if department is not None: empmatch['department']=department
    # Start from employees so zero-log employees contribute to headcount.
    pipe=[{'$match':empmatch},{'$lookup':{'from':'attendance_logs','let':{'code':'$emp_code'},'pipeline':[{'$match':{'$expr':{'$and':[{'$eq':['$emp_code','$$code']},{'$gte':['$date',start.isoformat()]},{'$lte':['$date',end.isoformat()]}]}}}], 'as':'logs'}},
      {'$unwind':{'path':'$logs','preserveNullAndEmptyArrays':True}},
      {'$group':{'_id':'$department','headcount':{'$addToSet':'$emp_code'},'present_days':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$lt':[{'$isoDayOfWeek':{'$dateFromString':{'dateString':'$logs.date'}}},6]}]},{'$cond':[{'$eq':[{'$ifNull':['$logs.half_day',False]},True]},0.5,1]},0]}},
      'work_hours_sum':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},'$logs.work_hours',0]}},
      'work_hours_count':{'$sum':{'$cond':[{'$and':[{'$in':['$logs.status',list(PRESENCE)]},{'$ne':[{'$ifNull':['$logs.work_hours',None]},None]}]},1,0]}},
      'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$logs.late_minutes',0]},0]},1,0]}},'total_late_minutes':{'$sum':{'$ifNull':['$logs.late_minutes',0]}},
      'leave_count':{'$sum':{'$cond':[{'$eq':['$logs.status','LEAVE']},1,0]}},'on_duty_count':{'$sum':{'$cond':[{'$eq':['$logs.status','ON_DUTY']},1,0]}}}},
      {'$project':{'_id':0,'department':'$_id','headcount':{'$size':'$headcount'},'present_days':1,'avg_work_hours':{'$cond':[{'$gt':['$work_hours_count',0]},{'$divide':['$work_hours_sum','$work_hours_count']},None]},'late_count':1,'total_late_minutes':1,'leave_count':1,'on_duty_count':1}}, {'$sort':{'department':1}}]
    items=[]
    for x in db.employees.aggregate(pipe):
        x['present_days']=round_half_up(x['present_days']); x['avg_work_hours']=round_half_up(x['avg_work_hours']) if x['avg_work_hours'] is not None else None; items.append(x)
    return {'month':month,'items':items}

@app.get('/analytics/leaderboard/late')
def late_leaderboard(month:str=Query(...,pattern=r'^\d{4}-(0[1-9]|1[0-2])$'),limit:int=Query(10,ge=1,le=50),department:Optional[str]=None):
    match,start,end=month_match(month)
    pipe=[{'$match':match},{'$group':{'_id':'$emp_code','total_late_minutes':{'$sum':{'$ifNull':['$late_minutes',0]}},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}},{'$match':{'total_late_minutes':{'$gt':0}}},
      {'$lookup':{'from':'employees','localField':'_id','foreignField':'emp_code','as':'employee'}},{'$unwind':'$employee'}]
    if department is not None: pipe.append({'$match':{'employee.department':department}})
    pipe += [{'$setWindowFields':{'sortBy':{'total_late_minutes':-1},'output':{'rank':{'$rank':{}}}}},{'$match':{'rank':{'$lte':limit}}},{'$sort':{'total_late_minutes':-1,'_id':1}}, {'$project':{'_id':0,'rank':1,'emp_code':'$_id','name':'$employee.name','department':'$employee.department','total_late_minutes':1,'late_count':1}}]
    return {'month':month,'items':list(db.attendance_logs.aggregate(pipe))}

@app.get('/analytics/departments/{department}/trend')
def department_trend(department:str,from_date:str=Query(...,alias='from'),to_date:str=Query(...,alias='to')):
    start=valid_date(from_date); end=valid_date(to_date)
    if end<start or (end-start).days+1>92: raise HTTPException(422,'to must be >= from and range must not exceed 92 days')
    if not db.employees.find_one({'department':department}): raise HTTPException(404,'unknown department')
    # Generate every calendar day in MongoDB ($range/$map), then look up that day's
    # logs and headcount. This also returns dates when the department has zero logs.
    days=(end-start).days+1
    pipe=[
      {'$documents':[{'start':datetime.combine(start,time.min,UTC),'days':days}]},
      {'$project':{'dates':{'$map':{'input':{'$range':[0,'$days']},'as':'offset','in':{'$dateAdd':{'startDate':'$start','unit':'day','amount':'$$offset'}}}}}},
      {'$unwind':'$dates'},
      {'$set':{'date':{'$dateToString':{'format':'%Y-%m-%d','date':'$dates','timezone':'UTC'}},'is_working_day':{'$lt':[{'$isoDayOfWeek':'$dates'},6]}}},
      {'$lookup':{'from':'attendance_logs','let':{'day':'$date'},'pipeline':[
        {'$match':{'$expr':{'$eq':['$date','$$day']}}},
        {'$lookup':{'from':'employees','localField':'emp_code','foreignField':'emp_code','as':'employee'}},
        {'$unwind':'$employee'},{'$match':{'employee.department':department}},
        {'$group':{'_id':None,'present_count':{'$sum':present_expr()},'late_count':{'$sum':{'$cond':[{'$gt':[{'$ifNull':['$late_minutes',0]},0]},1,0]}}}}
      ],'as':'daily'}},
      {'$lookup':{'from':'employees','let':{'d':'$date'},'pipeline':[
        {'$match':{'$expr':{'$and':[{'$eq':['$department',department]},{'$lte':['$joined_on','$$d']}]}}}, {'$count':'n'}
      ],'as':'hc'}},
      {'$set':{'headcount':{'$ifNull':[{'$arrayElemAt':['$hc.n',0]},0]},'present_count':{'$ifNull':[{'$arrayElemAt':['$daily.present_count',0]},0]},'late_count':{'$ifNull':[{'$arrayElemAt':['$daily.late_count',0]},0]}}},
      {'$set':{'attendance_rate':{'$cond':[{'$and':['$is_working_day',{'$gt':['$headcount',0]}]},{'$round':[{'$divide':['$present_count','$headcount']},4]},None]}}},
      {'$setWindowFields':{'sortBy':{'date':1},'output':{'moving_avg_7d':{'$avg':'$attendance_rate','window':{'documents':[-6,0]}}}}},
      {'$project':{'_id':0,'date':1,'is_working_day':1,'headcount':1,'present_count':1,'late_count':1,'attendance_rate':1,'moving_avg_7d':1}}, {'$sort':{'date':1}}
    ]
    items=list(db.aggregate(pipe,allowDiskUse=True))
    for x in items:
        if x['attendance_rate'] is not None: x['attendance_rate']=round_half_up(x['attendance_rate'],4)
        if x['moving_avg_7d'] is not None: x['moving_avg_7d']=round_half_up(x['moving_avg_7d'],4)
    return {'department':department,'items':items}

@app.get('/admin/explain/{endpoint}')
def explain_endpoint(endpoint:str,emp_code:Optional[str]=None,month:Optional[str]=None,department:Optional[str]=None,limit:int=Query(10,ge=1,le=50),date_from:Optional[str]=None,date_to:Optional[str]=None,status:Optional[str]=None,from_date:Optional[str]=Query(None,alias='from'),to_date:Optional[str]=Query(None,alias='to'),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100)):
    allowed={'attendance_list','employee_monthly','department_summary','late_leaderboard','department_trend'}
    if endpoint not in allowed: raise HTTPException(422,'unsupported endpoint')

    if endpoint == 'department_trend':
        if not department or not from_date or not to_date:
            raise HTTPException(422, 'department, from and to are required')

        a = valid_date(from_date)
        b = valid_date(to_date)

        if b < a or (b - a).days + 1 > 92:
            raise HTTPException(422, 'invalid trend range')

        pipe = [
            {'$match': {'date': {
                '$gte': a.isoformat(),
                '$lte': b.isoformat()
            }}},
            {'$group': {'_id': '$date', 'n': {'$sum': 1}}}
        ]
        collection = 'attendance_logs'
        result = db.command(
            'explain',
            {
                'aggregate': collection,
                'pipeline': pipe,
                'cursor': {},
                'allowDiskUse': True
            },
            verbosity='executionStats'
        )
        return {
            'endpoint': endpoint,
            'collection': collection,
            'explain': json_util.loads(json_util.dumps(result))
        }

    if endpoint=='attendance_list':
        q={}
        if emp_code:q['emp_code']=emp_code
        if date_from:q.setdefault('date',{})['$gte']=date_from
        if date_to:q.setdefault('date',{})['$lte']=date_to
        if status:q['status']=status
        query={'find':'attendance_logs','filter':q,'sort':{'date':-1,'emp_code':1},'skip':(page-1)*page_size,'limit':page_size}
        collection='attendance_logs'
        result=db.command('explain',query,verbosity='executionStats')
    else:
        if endpoint != 'department_trend' and not month: raise HTTPException(422,'month is required for this endpoint')
        match,st,en=month_match(month)
        if endpoint=='employee_monthly':
            if not emp_code: raise HTTPException(422,'emp_code is required')
            pipe=[{'$match':{'emp_code':emp_code,**match}},{'$group':{'_id':'$emp_code','n':{'$sum':1}}}]; collection='attendance_logs'
        elif endpoint=='department_summary':
            pipe=[{'$match':{'date':match['date']}},{'$group':{'_id':'$emp_code','n':{'$sum':1}}}]; collection='attendance_logs'
        elif endpoint=='late_leaderboard':
            pipe=[{'$match':match},{'$group':{'_id':'$emp_code','late':{'$sum':'$late_minutes'}}},{'$match':{'late':{'$gt':0}}}]; collection='attendance_logs'
        else:
            if not department or not from_date or not to_date: raise HTTPException(422,'department, from and to are required')
            a=valid_date(from_date); b=valid_date(to_date)
            if b<a or (b-a).days+1>92: raise HTTPException(422,'invalid trend range')
            pipe=[{'$match':{'date':{'$gte':a.isoformat(),'$lte':b.isoformat()}}},{'$group':{'_id':'$date','n':{'$sum':1}}}]; collection='attendance_logs'
        result=db.command('explain',{'aggregate':collection,'pipeline':pipe,'cursor':{},'allowDiskUse':True},verbosity='executionStats')
    return {'endpoint':endpoint,'collection':collection,'explain':json_util.loads(json_util.dumps(result))}


