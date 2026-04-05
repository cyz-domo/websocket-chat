import requests
from bs4 import BeautifulSoup

# 创建会话
session = requests.Session()

# 访问登录页面，获取CSRF token
login_url = 'http://127.0.0.1:8000/chat/login/'
response = session.get(login_url)
soup = BeautifulSoup(response.text, 'html.parser')
csrf_token = soup.find('input', {'name': 'csrfmiddlewaretoken'})['value']

# 准备登录数据
data = {
    'username': '菜狗子',
    'password': 'Chaitin@123',
    'csrfmiddlewaretoken': csrf_token
}

# 提交登录请求
response = session.post(login_url, data=data)
print(f"Login status code: {response.status_code}")

# 访问moments页面
moments_url = 'http://127.0.0.1:8000/chat/moments/'
response = session.get(moments_url)
print(f"Moments status code: {response.status_code}")
print(f"Moments page content: {response.text[:2000]}...")

# 检查服务器日志
print("\nChecking server logs...")
