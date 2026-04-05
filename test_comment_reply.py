import requests
from bs4 import BeautifulSoup
import json

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

# 访问朋友圈页面
moments_url = 'http://127.0.0.1:8000/chat/moments/'
response = session.get(moments_url)
print(f"Moments status code: {response.status_code}")

# 查找第一个动态的ID
soup = BeautifulSoup(response.text, 'html.parser')
moment_div = soup.find('div', class_='moment-item')
if moment_div:
    moment_id = moment_div.get('data-moment-id')
    print(f"Found moment with ID: {moment_id}")
    
    # 查找第一个评论的ID
    comment_div = moment_div.find('div', class_='moment-comment')
    if comment_div:
        comment_id = comment_div.get('data-comment-id')
        print(f"Found comment with ID: {comment_id}")
        
        # 回复评论
        comment_url = f'http://127.0.0.1:8000/chat/moments/{moment_id}/comment/'
        data = {
            'content': '测试回复评论',
            'parent_comment_id': comment_id,
            'csrfmiddlewaretoken': csrf_token
        }
        
        headers = {
            'X-Requested-With': 'XMLHttpRequest'
        }
        
        response = session.post(comment_url, data=data, headers=headers)
        print(f"Reply comment status code: {response.status_code}")
        print(f"Reply comment response: {response.json()}")
    else:
        print("No comments found")
else:
    print("No moments found")
