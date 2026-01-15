from django.shortcuts import render
from django.http import JsonResponse
from django.db import connection, transaction, IntegrityError
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import json
from .models import Vehicle, Driver, Order, ExceptionRecord, Fleet

# 辅助函数：统一返回JSON格式
def success_response(data=None, message="Success"):
    return JsonResponse({'code': 200, 'message': message, 'data': data})

def error_response(message="Error", code=400):
    return JsonResponse({'code': code, 'message': message})

# =============================================
# 1. 车辆管理接口
# =============================================

def vehicle_list(request):
    """获取车辆列表，支持按车队ID筛选"""
    fleet_id = request.GET.get('fleet_id')
    status = request.GET.get('status')
    
    queryset = Vehicle.objects.all()
    if fleet_id:
        queryset = queryset.filter(fleet_id=fleet_id)
    if status:
        queryset = queryset.filter(status=status)
        
    data = []
    for v in queryset:
        data.append({
            'plate_number': v.plate_number,
            'status': v.status,
            'max_weight': v.max_weight,
            'max_volume': v.max_volume,
            'fleet_name': v.fleet.fleet_name
        })
    return success_response(data)

@csrf_exempt
def vehicle_create(request):
    """录入车辆信息"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            Vehicle.objects.create(
                plate_number=data['plate_number'],
                fleet_id=data['fleet_id'],
                max_weight=data['max_weight'],
                max_volume=data['max_volume'],
                status=data.get('status', 'Idle')
            )
            return success_response(message="Vehicle created successfully")
        except Exception as e:
            return error_response(str(e))
    return error_response("Method not allowed", 405)

# =============================================
# 2. 司机管理接口
# =============================================

def driver_list(request):
    """获取司机列表"""
    fleet_id = request.GET.get('fleet_id')
    queryset = Driver.objects.all()
    if fleet_id:
        queryset = queryset.filter(fleet_id=fleet_id)
        
    data = []
    for d in queryset:
        data.append({
            'driver_id': d.driver_id,
            'name': d.name,
            'license_level': d.license_level,
            'phone': d.phone,
            'fleet_name': d.fleet.fleet_name
        })
    return success_response(data)

@csrf_exempt
def driver_create(request):
    """录入司机信息"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            Driver.objects.create(
                driver_id=data['driver_id'],
                name=data['name'],
                license_level=data['license_level'],
                phone=data.get('phone'),
                fleet_id=data['fleet_id']
            )
            return success_response(message="Driver created successfully")
        except Exception as e:
            return error_response(str(e))
    return error_response("Method not allowed", 405)

# =============================================
# 3. 核心业务：运单分配
# =============================================

def order_pending_list(request):
    """获取待处理运单"""
    orders = Order.objects.filter(status='Pending')
    data = [{'order_id': o.order_id, 'destination': o.destination, 'weight': o.cargo_weight} for o in orders]
    return success_response(data)

@csrf_exempt
def assign_order(request):
    """将运单分配给车辆"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            order_id = data['order_id']
            vehicle_plate = data['vehicle_plate']
            driver_id = data['driver_id']
            
            # 使用原生 SQL 捕获触发器错误，或者依赖 Django 捕获 DB 异常
            # 因为我们在数据库层面设置了 TRG_Load_Check 触发器，如果超载会抛错
            with transaction.atomic():
                order = Order.objects.get(order_id=order_id)
                order.vehicle_plate_id = vehicle_plate
                order.driver_id = driver_id
                order.status = 'Loading' # 状态流转
                order.start_time = timezone.now()
                order.save()
                
            return success_response(message="Order assigned successfully")
            
        except IntegrityError as e:
            # 捕获数据库触发器抛出的错误 (SQL Server通常会通过 IntegrityError 或 DataError 传递回来)
            return error_response(f"Assignment failed: Database Constraint/Trigger Error - {str(e)}")
        except Order.DoesNotExist:
            return error_response("Order not found")
        except Exception as e:
            return error_response(str(e))
    return error_response("Method not allowed", 405)

# =============================================
# 4. 异常管理
# =============================================

@csrf_exempt
def exception_create(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            ExceptionRecord.objects.create(
                vehicle_plate_id=data['vehicle_plate'],
                driver_id=data['driver_id'],
                exception_type=data['exception_type'],
                specific_event=data['specific_event'],
                description=data.get('description'),
                handle_status='Unprocessed'
            )
            # 数据库触发器 TRG_Exception_Flag 会自动将车辆状态改为 Exception
            return success_response(message="Exception recorded")
        except Exception as e:
            return error_response(str(e))
    return error_response("Method not allowed", 405)

# =============================================
# 5. 报表与存储过程调用
# =============================================

def fleet_monthly_report(request):
    """调用存储过程 SP_Calc_Fleet_Monthly_Report"""
    fleet_id = request.GET.get('fleet_id')
    report_date = request.GET.get('date') # Format: YYYY-MM-DD
    
    if not fleet_id or not report_date:
        return error_response("Missing parameters")

    with connection.cursor() as cursor:
        # 注意：不同数据库驱动调用存储过程语法不同
        # SQL Server: EXEC SP_Calc_Fleet_Monthly_Report %s, %s
        cursor.execute("EXEC SP_Calc_Fleet_Monthly_Report %s, %s", [fleet_id, report_date])
        result = cursor.fetchall() # 获取结果集
        
    # 格式化结果
    columns = [col[0] for col in cursor.description]
    data = [dict(zip(columns, row)) for row in result]
    
    return success_response(data)

def driver_performance(request):
    """调用存储过程 SP_Get_Driver_Performance"""
    driver_id = request.GET.get('driver_id')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if not driver_id:
        return error_response("Missing driver_id")
        
    with connection.cursor() as cursor:
        cursor.execute("EXEC SP_Get_Driver_Performance %s, %s, %s", [driver_id, start_date, end_date])
        # 存储过程返回两个结果集，Django cursor 通常只能获取第一个
        # 如果需要获取多个，需要底层驱动支持 .nextset()
        
        # 获取第一个结果集 (Performance Summary)
        summary_result = cursor.fetchall()
        summary_cols = [col[0] for col in cursor.description]
        summary_data = [dict(zip(summary_cols, row)) for row in summary_result]
        
        exceptions_data = []
        if cursor.nextset(): # 尝试获取第二个结果集 (Exception details)
            details_result = cursor.fetchall()
            if cursor.description:
                details_cols = [col[0] for col in cursor.description]
                exceptions_data = [dict(zip(details_cols, row)) for row in details_result]
            
    return success_response({
        'summary': summary_data,
        'exceptions': exceptions_data
    })
