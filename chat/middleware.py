# chat/middleware.py
from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import logout
from django.utils.deprecation import MiddlewareMixin
from .models import UserSession


class CheckUserSessionMiddleware:
    """检查用户session是否有效"""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        if request.user.is_authenticated:
            current_session_key = request.session.session_key
            
            # 如果没有会话键，跳过检查
            if not current_session_key:
                return self.get_response(request)
            
            # 跳过登录页面和注册页面的检查
            if request.path in ['/chat/login/', '/chat/register/']:
                return self.get_response(request)
            
            try:
                valid_session = UserSession.objects.filter(user=request.user, session_key=current_session_key).exists()

                if not valid_session:
                    latest_user_session = UserSession.objects.filter(user=request.user).order_by('-created_at').first()
                    if latest_user_session is None:
                        UserSession.objects.create(user=request.user, session_key=current_session_key)
                    elif latest_user_session.session_key == current_session_key:
                        pass
                    else:
                        logout(request)
                        messages.error(request, '您的账号已在其他地方登录，请重新登录')
                        return redirect('login')
            except Exception:
                # 如果查询出错，跳过检查
                pass
        
        return self.get_response(request)
