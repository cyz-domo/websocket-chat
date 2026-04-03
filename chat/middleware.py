# chat/middleware.py
from pathlib import Path
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


class InjectMobileBridgeMiddleware(MiddlewareMixin):
    @staticmethod
    def get_bridge_script_src():
        script_path = Path(__file__).resolve().parent.parent / 'static' / 'mobile-bridge.js'
        try:
            version = int(script_path.stat().st_mtime)
        except OSError:
            version = 0
        return f'/static/mobile-bridge.js?v={version}'

    def process_response(self, request, response):
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            return response
        if getattr(response, 'streaming', False):
            return response

        try:
            content = response.content.decode(response.charset or 'utf-8')
        except Exception:
            return response

        if '/static/mobile-bridge.js' in content:
            return response

        marker = '</body>'
        if marker not in content.lower():
            return response

        script_tag = f'<script src="{self.get_bridge_script_src()}"></script>'
        lower_content = content.lower()
        marker_index = lower_content.rfind(marker)
        updated_content = f'{content[:marker_index]}{script_tag}{content[marker_index:]}'
        response.content = updated_content.encode(response.charset or 'utf-8')
        if 'Content-Length' in response.headers:
            response.headers['Content-Length'] = str(len(response.content))
        return response
