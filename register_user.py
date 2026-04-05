import requests
from bs4 import BeautifulSoup

# 首先获取注册页面，获取CSRF token
session = requests.Session()
register_url = 'http://127.0.0.1:8000/chat/register/'
response = session.get(register_url)

# 解析HTML，提取CSRF token
soup = BeautifulSoup(response.text, 'html.parser')
csrf_token = soup.find('input', {'name': 'csrfmiddlewaretoken'})['value']

# 准备注册数据
data = {
    'username': 'testuser',
    'password1': 'testpassword',
    'password2': 'testpassword',
    'csrfmiddlewaretoken': csrf_token
}

# 提交注册请求
response = session.post(register_url, data=data)

# 打印响应
print(f"Status code: {response.status_code}")
print(f"Response content: {response.text[:500]}...")

# 保存cookies
with open('cookies.txt', 'w') as f:
    for cookie in session.cookies:
        f.write(f"{cookie.name}={cookie.value}\n")

# 尝试访问moments页面
moments_url = 'http://127.0.0.1:8000/chat/moments/'
response = session.get(moments_url)
print(f"\nMoments page status code: {response.status_code}")
print(f"Moments page content: {response.text[:1000]}...")
