import io
import asyncio
<<<<<<< Updated upstream
import json
=======
from unittest.mock import patch
>>>>>>> Stashed changes

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.urls import reverse
from PIL import Image
from unittest.mock import AsyncMock

from .consumers import ChatConsumer
from .models import (
    DirectConversation,
    DirectConversationState,
    DirectMessage,
    FriendRequest,
    Friendship,
    Message,
    Room,
    RoomInvitation,
    RoomJoinRequest,
    RoomMembership,
    UserChatProfile,
    SiteConfiguration,
    UsernameAlias,
    UserLocation,
    UserSession,
)
from .services import ChinaAddressNormalizer, ChinaDivisionRepository, GlobalReverseGeocodeService, UserLocationService
from .origin_middleware import DynamicOriginSettingsMiddleware


class ChatAppearanceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='secret123')
        self.room = Room.objects.create(name='test-room', created_by=self.user)
        self.factory = RequestFactory()

    def login_with_valid_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session.save()
        UserSession.objects.update_or_create(
            user=self.user,
            defaults={'session_key': session.session_key},
        )

    def test_room_view_creates_default_chat_profile(self):
        self.login_with_valid_session()

        response = self.client.get(reverse('chat_room', args=[self.room.name]))

        self.assertEqual(response.status_code, 200)
        profile = UserChatProfile.objects.get(user=self.user)
        self.assertTrue(profile.public_id)
        self.assertEqual(profile.display_name, 'alice')
        self.assertEqual(profile.avatar_label, '')
        self.assertEqual(profile.color_theme, 'amber')
        self.assertEqual(profile.bubble_style, 'soft')
        self.assertTrue(profile.show_location)

    def test_profile_settings_view_updates_chat_profile(self):
        self.login_with_valid_session()

        response = self.client.post(
            reverse('profile_settings'),
            {
                'display_name': '阿来同学',
                'username': 'alice',
                'avatar_label': '阿来',
                'color_theme': 'ocean',
                'bubble_style': 'glass',
                'show_location': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = UserChatProfile.objects.get(user=self.user)
        self.assertEqual(profile.display_name, '阿来同学')
        self.assertEqual(profile.avatar_label, '阿来')
        self.assertEqual(profile.color_theme, 'ocean')
        self.assertEqual(profile.bubble_style, 'glass')
        self.assertTrue(profile.show_location)

    def test_profile_settings_can_upload_avatar_image(self):
        self.login_with_valid_session()
        image_buffer = io.BytesIO()
        Image.new('RGB', (1200, 1200), color=(210, 120, 80)).save(image_buffer, format='JPEG', quality=95)
        image_buffer.seek(0)
        uploaded_file = SimpleUploadedFile('avatar.jpg', image_buffer.getvalue(), content_type='image/jpeg')

        response = self.client.post(
            reverse('profile_settings'),
            {
                'display_name': '阿来',
                'username': 'alice',
                'friend_id': 'alice001',
                'avatar_label': '阿来',
                'bio': '测试头像上传',
                'color_theme': 'ocean',
                'bubble_style': 'glass',
                'show_location': 'on',
                'avatar_image': uploaded_file,
            },
        )

        self.assertEqual(response.status_code, 302)
        profile = UserChatProfile.objects.get(user=self.user)
        self.assertTrue(bool(profile.avatar_image))
        self.assertTrue(profile.avatar_url)
        self.assertLessEqual(profile.avatar_image.size, 1024 * 1024)

    @patch('chat.views.GeoIPService.save_precise_user_location')
    def test_update_precise_location_endpoint(self, save_precise_user_location):
        self.login_with_valid_session()
        UserLocation.objects.create(
            user=self.user,
            ip_address='8.8.8.8',
            country='中国',
            region='上海市',
            city='上海市',
            district='浦东新区',
            township='曹路镇',
            latitude=31.2304,
            longitude=121.4737,
            timezone='Asia/Shanghai',
        )
        save_precise_user_location.return_value = True

        response = self.client.post(
            reverse('update_precise_location'),
            data='{"latitude":31.2304,"longitude":121.4737}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {'ok': True, 'location': '上海市 · 浦东新区 · 曹路镇'},
        )

    def test_send_friend_request_creates_pending_request(self):
        self.login_with_valid_session()
        other = User.objects.create_user(username='bob', password='secret123')
        other_profile = UserChatProfile.objects.create(user=other, friend_id='bobfriend')

        response = self.client.post(
            reverse('send_friend_request'),
            {'friend_id': other_profile.friend_id, 'next': reverse('chat_index')},
        )

        self.assertEqual(response.status_code, 302)
        request_obj = FriendRequest.objects.get(sender=self.user, recipient=other)
        self.assertEqual(request_obj.status, FriendRequest.STATUS_PENDING)

    def test_accept_friend_request_creates_friendship(self):
        self.login_with_valid_session()
        other = User.objects.create_user(username='bob', password='secret123')
        friend_request = FriendRequest.objects.create(sender=other, recipient=self.user)

        response = self.client.post(
            reverse('respond_friend_request', args=[friend_request.id]),
            {'action': 'accept'},
        )

        self.assertEqual(response.status_code, 302)
        friend_request.refresh_from_db()
        self.assertEqual(friend_request.status, FriendRequest.STATUS_ACCEPTED)
        self.assertTrue(Friendship.objects.filter(user=self.user, friend=other).exists())
        self.assertTrue(Friendship.objects.filter(user=other, friend=self.user).exists())

    def test_profile_settings_rejects_short_friend_id(self):
        self.login_with_valid_session()

        response = self.client.post(
            reverse('profile_settings'),
            {'display_name': 'alice', 'username': 'alice', 'friend_id': 'short1', 'avatar_label': '', 'bio': ''},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any('8 到 11 位' in str(message) for message in messages_list))

    def test_site_settings_can_update_allowed_hosts(self):
        self.login_with_valid_session()
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save(update_fields=['is_staff', 'is_superuser'])

        response = self.client.post(
            reverse('admin_site_settings'),
            {
                'site_title': 'animal chat',
                'allowed_hosts': 'chat1.ufgc.eu.cc\nchat.example.com',
                'trusted_origins': 'https://chat1.ufgc.eu.cc',
                'cors_allowed_origins': 'https://chat1.ufgc.eu.cc',
                'chat_attachment_max_mb': 50,
            },
        )

        self.assertEqual(response.status_code, 302)
        site_config = SiteConfiguration.get_solo()
        self.assertEqual(site_config.allowed_hosts, 'chat1.ufgc.eu.cc\nchat.example.com')

    @patch('chat.origin_middleware.settings.DEFAULT_ALLOWED_HOSTS', ['localhost', '127.0.0.1'])
    @patch('chat.origin_middleware.settings.DEFAULT_CSRF_TRUSTED_ORIGINS', [])
    @patch('chat.origin_middleware.settings.DEFAULT_CORS_ALLOWED_ORIGINS', [])
    def test_dynamic_origin_middleware_merges_allowed_hosts(self):
        SiteConfiguration.get_solo()
        SiteConfiguration.objects.update(
            allowed_hosts='chat1.ufgc.eu.cc\n.example.com'
        )

        middleware = DynamicOriginSettingsMiddleware(lambda request: None)
        middleware(self.factory.get('/'))

        from django.conf import settings

        self.assertIn('chat1.ufgc.eu.cc', settings.ALLOWED_HOSTS)
        self.assertIn('.example.com', settings.ALLOWED_HOSTS)

    def test_profile_settings_can_change_username_and_sync_room_history(self):
        self.login_with_valid_session()
        Message.objects.create(room=self.room, user=self.user, username='alice', message='hello')

        response = self.client.post(
            reverse('profile_settings'),
            {
                'username': '阿狸',
                'display_name': '阿狸',
                'friend_id': '',
                'avatar_label': '',
                'bio': '新的中文用户名',
                'color_theme': 'amber',
                'bubble_style': 'soft',
                'show_location': 'on',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, '阿狸')
        self.assertTrue(Message.objects.filter(user=self.user, username='阿狸').exists())
        self.assertTrue(UsernameAlias.objects.filter(user=self.user, username='alice').exists())

    def test_profile_settings_rejects_duplicate_username_case_insensitive(self):
        self.login_with_valid_session()
        User.objects.create_user(username='Alice2', password='secret123')

        response = self.client.post(
            reverse('profile_settings'),
            {
                'username': 'alice2',
                'display_name': 'alice2',
                'friend_id': 'alice001',
                'avatar_label': '',
                'bio': '',
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages_list = list(response.context['messages'])
        self.assertTrue(any('用户名已经被使用了' in str(message) for message in messages_list))

    def test_old_profile_url_redirects_to_new_username(self):
        self.login_with_valid_session()
        self.client.post(
            reverse('profile_settings'),
            {
                'username': '阿狸',
                'display_name': '阿狸',
                'friend_id': '',
                'avatar_label': '',
                'bio': '',
            },
        )

        response = self.client.get(reverse('user_profile_legacy', args=['alice']))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('user_profile', args=[UserChatProfile.objects.get(user=self.user).public_id]))

    def test_old_direct_chat_url_redirects_to_new_username(self):
        self.login_with_valid_session()
        other = User.objects.create_user(username='bob', password='secret123')
        Friendship.objects.create(user=self.user, friend=other)
        Friendship.objects.create(user=other, friend=self.user)
        other.username = '波波'
        other.save(update_fields=['username'])
        UsernameAlias.objects.create(user=other, username='bob')

        response = self.client.get(reverse('direct_chat_legacy', args=['bob']))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('direct_chat', args=[UserChatProfile.objects.get(user=other).public_id]))

    def test_mark_direct_read_accepts_old_username_alias(self):
        self.login_with_valid_session()
        other = User.objects.create_user(username='bob', password='secret123')
        Friendship.objects.create(user=self.user, friend=other)
        Friendship.objects.create(user=other, friend=self.user)
        other.username = '波波'
        other.save(update_fields=['username'])
        UsernameAlias.objects.create(user=other, username='bob')

        response = self.client.post(reverse('mark_direct_read_legacy', args=['bob']))

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {'ok': True})

    def test_removed_room_member_can_still_open_room_but_is_read_only(self):
        other = User.objects.create_user(username='bob', password='secret123')
        RoomMembership.objects.create(room=self.room, user=other, is_active=False)
        self.client.force_login(other)

        response = self.client.get(reverse('chat_room', args=[self.room.name]))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_removed_from_room'])

    def test_removed_room_member_can_open_room_history_page(self):
        other = User.objects.create_user(username='bob2', password='secret123')
        RoomMembership.objects.create(room=self.room, user=other, is_active=False)
        self.client.force_login(other)

        response = self.client.get(reverse('room_history', args=[self.room.name]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '聊天记录')

    def test_room_total_members_uses_active_memberships(self):
        other = User.objects.create_user(username='bob', password='secret123')
        RoomMembership.objects.create(room=self.room, user=self.user, is_active=True)
        RoomMembership.objects.create(room=self.room, user=other, is_active=False)

        self.assertEqual(self.room.total_members, 1)

    def test_room_generates_room_id_and_defaults_to_approval_join_policy(self):
        self.assertRegex(self.room.room_id, r'^\d{12}$')
        self.assertEqual(self.room.join_policy, Room.JOIN_POLICY_APPROVAL)

    def test_index_only_shows_rooms_for_joined_members(self):
        other = User.objects.create_user(username='bob', password='secret123')
        hidden_room = Room.objects.create(name='hidden-room', created_by=other)
        RoomMembership.objects.create(room=hidden_room, user=other, is_active=True)
        RoomMembership.objects.create(room=self.room, user=self.user, is_active=True)
        self.login_with_valid_session()

        response = self.client.get(reverse('chat_index'))

        self.assertEqual(response.status_code, 200)
        room_names = [item['room'].name for item in response.context['room_items']]
        self.assertIn(self.room.name, room_names)
        self.assertNotIn(hidden_room.name, room_names)

    def test_non_member_cannot_open_room_page(self):
        other = User.objects.create_user(username='bob', password='secret123')
        hidden_room = Room.objects.create(name='hidden-room', created_by=other)
        RoomMembership.objects.create(room=hidden_room, user=other, is_active=True)
        self.login_with_valid_session()

        response = self.client.get(reverse('chat_room', args=[hidden_room.name]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '你还不是这个群聊的成员')

    def test_create_room_supports_join_policy(self):
        self.login_with_valid_session()

        response = self.client.post(
            reverse('create_room'),
            {
                'room_name': 'approval-room',
                'room_avatar': '💬',
                'room_description': '需要审批',
                'join_policy': Room.JOIN_POLICY_OPEN,
            },
        )

        self.assertEqual(response.status_code, 302)
        created_room = Room.objects.get(name='approval-room')
        self.assertEqual(created_room.join_policy, Room.JOIN_POLICY_OPEN)

    def test_join_open_room_creates_active_membership(self):
        owner = User.objects.create_user(username='owner', password='secret123')
        open_room = Room.objects.create(name='open-room', created_by=owner, join_policy=Room.JOIN_POLICY_OPEN)
        RoomMembership.objects.create(room=open_room, user=owner, is_active=True)
        self.login_with_valid_session()

        response = self.client.post(reverse('join_room', args=[open_room.room_id]))

        self.assertEqual(response.status_code, 302)
        membership = RoomMembership.objects.get(room=open_room, user=self.user)
        self.assertTrue(membership.is_active)

    def test_join_approval_room_creates_pending_request(self):
        owner = User.objects.create_user(username='owner2', password='secret123')
        approval_room = Room.objects.create(name='approval-room-2', created_by=owner, join_policy=Room.JOIN_POLICY_APPROVAL)
        RoomMembership.objects.create(room=approval_room, user=owner, is_active=True)
        self.login_with_valid_session()

        response = self.client.post(reverse('join_room', args=[approval_room.room_id]), {'note': '想加入聊聊'})

        self.assertEqual(response.status_code, 302)
        join_request = RoomJoinRequest.objects.get(room=approval_room, requester=self.user)
        self.assertEqual(join_request.status, RoomJoinRequest.STATUS_PENDING)

    def test_removed_member_can_reapply_and_reset_old_accepted_request(self):
        owner = User.objects.create_user(username='owner3', password='secret123')
        approval_room = Room.objects.create(name='approval-room-3', created_by=owner, join_policy=Room.JOIN_POLICY_APPROVAL)
        RoomMembership.objects.create(room=approval_room, user=owner, is_active=True)
        RoomMembership.objects.create(room=approval_room, user=self.user, is_active=False)
        RoomJoinRequest.objects.create(
            room=approval_room,
            requester=self.user,
            status=RoomJoinRequest.STATUS_ACCEPTED,
        )
        self.login_with_valid_session()

        response = self.client.post(reverse('join_room', args=[approval_room.room_id]), {'note': '重新申请加入'})

        self.assertEqual(response.status_code, 302)
        join_request = RoomJoinRequest.objects.get(room=approval_room, requester=self.user)
        self.assertEqual(join_request.status, RoomJoinRequest.STATUS_PENDING)
        self.assertEqual(join_request.note, '重新申请加入')


class DirectMessageFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='secret123')
        self.other = User.objects.create_user(username='bobby', password='secret123')
        UserChatProfile.objects.create(user=self.user, friend_id='alice001')
        UserChatProfile.objects.create(user=self.other, friend_id='bobby001')
        Friendship.objects.create(user=self.user, friend=self.other)
        Friendship.objects.create(user=self.other, friend=self.user)

    def login_with_valid_session(self):
        self.client.force_login(self.user)
        session = self.client.session
        session.save()
        UserSession.objects.update_or_create(
            user=self.user,
            defaults={'session_key': session.session_key},
        )

    def test_user_profile_shows_direct_chat_for_friends(self):
        self.login_with_valid_session()

        response = self.client.get(reverse('user_profile', args=[self.other.chat_profile.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '直接聊天')

    def test_direct_chat_persists_messages(self):
        self.login_with_valid_session()

        response = self.client.post(
            reverse('direct_chat', args=[self.other.chat_profile.public_id]),
            {'action': 'send', 'content': '你好，私聊一下'},
        )

        self.assertEqual(response.status_code, 302)
        message = DirectMessage.objects.get()
        self.assertEqual(message.sender, self.user)
        self.assertEqual(message.content, '你好，私聊一下')

    def test_direct_history_page_opens_for_friends(self):
        self.login_with_valid_session()
        response = self.client.get(reverse('direct_history', args=[self.other.chat_profile.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '聊天记录')

    def test_clear_history_only_hides_messages_for_current_user(self):
        self.login_with_valid_session()
        self.client.post(reverse('direct_chat', args=[self.other.chat_profile.public_id]), {'action': 'send', 'content': '第一条'})
        self.client.post(reverse('direct_chat', args=[self.other.chat_profile.public_id]), {'action': 'clear_history'})
        self.client.post(reverse('direct_chat', args=[self.other.chat_profile.public_id]), {'action': 'send', 'content': '第二条'})

        response = self.client.get(reverse('direct_chat', args=[self.other.chat_profile.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '第一条')
        self.assertContains(response, '第二条')
        state = DirectConversationState.objects.get(user=self.user)
        self.assertIsNotNone(state.cleared_at)

    def test_remove_friend_breaks_direct_chat_access(self):
        self.login_with_valid_session()

        response = self.client.post(
            reverse('remove_friend', args=[self.other.chat_profile.public_id]),
            {'next': reverse('friends')},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Friendship.objects.filter(user=self.user, friend=self.other).exists())
        self.assertFalse(Friendship.objects.filter(user=self.other, friend=self.user).exists())

        blocked = self.client.get(reverse('direct_chat', args=[self.other.chat_profile.public_id]), follow=True)
        self.assertContains(blocked, '你们还不是好友')


class ChatMessageSerializationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='secret123')
        self.room = Room.objects.create(name='test-room', created_by=self.user)
        self.consumer = ChatConsumer()
        self.consumer.room_name = self.room.name

    def test_save_message_stores_location_label(self):
        UserLocation.objects.create(
            user=self.user,
            ip_address='8.8.8.8',
            country='中国',
            region='浙江省',
            city='杭州市',
            district='西湖区',
            township='古荡街道',
            latitude=30.2741,
            longitude=120.1551,
            timezone='Asia/Shanghai',
        )
        UserChatProfile.objects.create(user=self.user, color_theme='forest', bubble_style='rounded', show_location=True)
        self.consumer.scope = {'user': self.user}

        payload = self.consumer.save_message.__wrapped__(self.consumer, '你好', self.user.username)

        msg = Message.objects.get(room=self.room)
        self.assertEqual(msg.location_label, '浙江省 · 杭州市 · 西湖区 · 古荡街道')
        self.assertEqual(payload['location'], '浙江省 · 杭州市 · 西湖区 · 古荡街道')
        self.assertEqual(payload['avatar_label'], 'AL')
        self.assertEqual(payload['appearance']['theme'], 'forest')
        self.assertEqual(payload['appearance']['style'], 'rounded')

    def test_save_message_omits_location_when_hidden(self):
        UserLocation.objects.create(
            user=self.user,
            ip_address='8.8.8.8',
            country='中国',
            region='浙江省',
            city='杭州市',
            district='西湖区',
            township='古荡街道',
            latitude=30.2741,
            longitude=120.1551,
            timezone='Asia/Shanghai',
        )
        UserChatProfile.objects.create(user=self.user, color_theme='rose', bubble_style='soft', show_location=False)
        self.consumer.scope = {'user': self.user}

        payload = self.consumer.save_message.__wrapped__(self.consumer, '隐藏位置', self.user.username)

        msg = Message.objects.get(message='隐藏位置')
        self.assertEqual(msg.location_label, '')
        self.assertEqual(payload['location'], '')

    def test_custom_avatar_label_is_used_in_message_payload(self):
        UserChatProfile.objects.create(
            user=self.user,
            avatar_label='小明',
            color_theme='rose',
            bubble_style='soft',
            show_location=False,
        )
        self.consumer.scope = {'user': self.user}

        payload = self.consumer.save_message.__wrapped__(self.consumer, '带头像', self.user.username)

        self.assertEqual(payload['avatar_label'], '小明')

    def test_avatar_url_is_included_in_message_payload(self):
        image_buffer = io.BytesIO()
        Image.new('RGB', (120, 120), color=(120, 160, 210)).save(image_buffer, format='JPEG', quality=88)
        image_buffer.seek(0)
        profile = UserChatProfile.objects.create(user=self.user, color_theme='rose', bubble_style='soft', show_location=False)
        profile.avatar_image.save(
            'avatars/test-avatar.jpg',
            SimpleUploadedFile('avatar.jpg', image_buffer.getvalue(), content_type='image/jpeg'),
            save=True,
        )
        self.consumer.scope = {'user': self.user}

        payload = self.consumer.save_message.__wrapped__(self.consumer, '带图头像', self.user.username)

        self.assertIn('/media/avatars/', payload['avatar_url'])

    def test_display_label_avoids_duplicate_city_names(self):
        location = UserLocation.objects.create(
            user=self.user,
            ip_address='8.8.4.4',
            country='中国',
            region='上海市',
            city='上海市',
            district='浦东新区',
            township='曹路镇',
            latitude=31.2304,
            longitude=121.4737,
            timezone='Asia/Shanghai',
        )

        self.assertEqual(location.display_label, '上海市 · 浦东新区 · 曹路镇')

    def test_serialize_message_prefers_current_localized_location(self):
        UserLocation.objects.create(
            user=self.user,
            ip_address='1.2.3.4',
            country='中国',
            region='上海市',
            city='上海市',
            district='浦东新区',
            township='曹路镇',
            latitude=31.2304,
            longitude=121.4737,
            timezone='Asia/Shanghai',
        )
        UserChatProfile.objects.create(user=self.user, avatar_label='ypy', color_theme='forest', bubble_style='glass', show_location=True)
        message = Message.objects.create(
            room=self.room,
            user=self.user,
            username='ypy',
            message='旧消息',
            location_label='Shanghai',
        )

        payload = self.consumer.serialize_message(message)

        self.assertEqual(payload['location'], '上海市 · 浦东新区 · 曹路镇')


class ChatConsumerDisconnectTests(TestCase):
    def test_disconnect_sends_materialized_user_list(self):
        consumer = ChatConsumer()
        consumer.room_name = 'test-room'
        consumer.room_group_name = ChatConsumer.build_group_name(consumer.room_name)
        consumer.channel_name = 'test-channel'
        consumer.is_group_member = True
        consumer.channel_layer = type(
            'Layer',
            (),
            {
                'group_send': AsyncMock(),
                'group_discard': AsyncMock(),
            },
        )()
        consumer.get_users_dict = AsyncMock(return_value={'alice': {'is_online': True}})
        consumer.room_users = {
            consumer.room_name: {
                consumer.channel_name: {'username': 'alice'}
            }
        }

        asyncio.run(consumer.disconnect(1000))

        consumer.channel_layer.group_send.assert_awaited_once_with(
            consumer.room_group_name,
            {
                'type': 'user_list',
                'users': {'alice': {'is_online': True}},
            },
        )


class MobilePushTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alice', password='secret123')
        self.other_user = User.objects.create_user(username='bob', password='secret123')
        self.room = Room.objects.create(name='push-room', created_by=self.user)
        RoomMembership.objects.create(room=self.room, user=self.user, is_active=True)
        RoomMembership.objects.create(room=self.room, user=self.other_user, is_active=True)
        self.other_profile = UserChatProfile.objects.create(user=self.other_user, friend_id='bobfriend')


class LocationServiceTests(TestCase):
    def test_china_normalizer_keeps_global_support_and_normalizes_china(self):
        china = ChinaAddressNormalizer.normalize({
            'country': 'China',
            'region': 'Shanghai',
            'city': 'Shanghai',
            'district': 'Pudong',
            'latitude': 0,
            'longitude': 0,
            'timezone': '',
        })
        global_location = ChinaAddressNormalizer.normalize({
            'country': 'Japan',
            'region': 'Tokyo',
            'city': 'Shibuya',
            'district': '',
            'latitude': 0,
            'longitude': 0,
            'timezone': '',
        })

        self.assertEqual(china['country'], '中国')
        self.assertEqual(china['region'], 'Shanghai')
        self.assertEqual(global_location['country'], 'Japan')
        self.assertEqual(global_location['city'], 'Shibuya')

    @patch('chat.services.location_service.requests.get')
    def test_ip_lookup_flows_through_global_plus_china_enhancement(self, mock_get):
        mock_get.return_value.json.return_value = {
            'status': 'success',
            'country': 'China',
            'regionName': 'Shanghai',
            'city': 'Shanghai',
            'district': 'Pudong New Area',
            'lat': 31.2304,
            'lon': 121.4737,
            'timezone': 'Asia/Shanghai',
        }

        location = UserLocationService.get_location_by_ip('1.2.3.4')

        self.assertEqual(location['country'], '中国')
        self.assertEqual(location['region'], 'Shanghai')
        self.assertEqual(location['district'], 'Pudong New Area')

    def test_china_division_repository_canonicalizes_local_dataset(self):
        dataset = ChinaDivisionRepository.build_dataset(
            provinces=[{'code': '310000', 'name': '上海市', 'province': '31'}],
            cities=[{'code': '310100', 'name': '上海市', 'province': '31', 'city': '01'}],
            areas=[{'code': '310115', 'name': '浦东新区', 'province': '31', 'city': '01', 'area': '15'}],
        )

        with patch.object(ChinaDivisionRepository, 'load_dataset', return_value=dataset):
            canonical = ChinaDivisionRepository.canonicalize(region='上海', city='上海', district='浦东新区')

        self.assertEqual(canonical['region'], '上海市')
        self.assertEqual(canonical['city'], '上海市')
        self.assertEqual(canonical['district'], '浦东新区')

    def test_china_normalizer_uses_local_repository_when_available(self):
        dataset = ChinaDivisionRepository.build_dataset(
            provinces=[{'code': '310000', 'name': '上海市', 'province': '31'}],
            cities=[{'code': '310100', 'name': '上海市', 'province': '31', 'city': '01'}],
            areas=[{'code': '310115', 'name': '浦东新区', 'province': '31', 'city': '01', 'area': '15'}],
        )

        with patch.object(ChinaDivisionRepository, 'load_dataset', return_value=dataset):
            normalized = ChinaAddressNormalizer.normalize({
                'country': '中国',
                'region': '上海',
                'city': '上海',
                'district': '浦东新区',
                'latitude': 0,
                'longitude': 0,
                'timezone': '',
            })

        self.assertEqual(normalized['region'], '上海市')
        self.assertEqual(normalized['city'], '上海市')
        self.assertEqual(normalized['district'], '浦东新区')

    @patch.object(GlobalReverseGeocodeService, 'reverse_geocode_secondary')
    @patch('chat.services.reverse_geocode_service.requests.get')
    def test_reverse_geocode_uses_secondary_source_when_primary_is_too_coarse(self, mock_get, mock_secondary):
        mock_get.return_value.json.return_value = {
            'address': {
                'country': '中国',
            }
        }
        mock_secondary.return_value = {
            'country': '中国',
            'region': '上海市',
            'city': '上海市',
            'district': '浦东新区',
            'township': '曹路镇',
            'latitude': 31.2,
            'longitude': 121.6,
            'timezone': '',
        }

        location = GlobalReverseGeocodeService.reverse_geocode(31.2, 121.6)

        self.assertEqual(location['region'], '上海市')
        self.assertEqual(location['district'], '浦东新区')
        self.assertEqual(location['township'], '曹路镇')
