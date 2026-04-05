from django.contrib.auth.models import User

# 创建用户
user, created = User.objects.get_or_create(username='testuser')
if created:
    user.set_password('testpassword')
    user.save()
    print('User created successfully')
else:
    print('User already exists')
    user.set_password('testpassword')
    user.save()
    print('Password updated')

print(f'User: {user.username}')
print(f'Created: {created}')
