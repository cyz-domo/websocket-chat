from django.contrib import admin

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
    SiteConfiguration,
    UserChatProfile,
    UserLocation,
    UserSession,
)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'room_id', 'join_policy', 'created_by', 'avatar', 'avatar_image', 'created_at')
    list_filter = ('join_policy',)
    search_fields = ('name', 'room_id', 'created_by__username')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('room', 'username', 'message_type', 'location_label', 'timestamp')
    search_fields = ('room__name', 'username', 'message')
    list_filter = ('message_type', 'timestamp')


@admin.register(RoomMembership)
class RoomMembershipAdmin(admin.ModelAdmin):
    list_display = ('room', 'user', 'is_active', 'is_admin', 'joined_at', 'removed_at')
    list_filter = ('is_active', 'is_admin')
    search_fields = ('room__name', 'user__username')


@admin.register(RoomJoinRequest)
class RoomJoinRequestAdmin(admin.ModelAdmin):
    list_display = ('room', 'requester', 'status', 'created_at', 'responded_at')
    list_filter = ('status',)
    search_fields = ('room__name', 'room__room_id', 'requester__username')


@admin.register(RoomInvitation)
class RoomInvitationAdmin(admin.ModelAdmin):
    list_display = ('room', 'invited_user', 'invited_by', 'status', 'created_at', 'responded_at')
    list_filter = ('status',)
    search_fields = ('room__name', 'room__room_id', 'invited_user__username', 'invited_by__username')


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ('id', 'allow_all_cors', 'updated_at')
    search_fields = ('allowed_hosts', 'trusted_origins', 'cors_allowed_origins')


@admin.register(UserLocation)
class UserLocationAdmin(admin.ModelAdmin):
    list_display = ('user', 'township', 'district', 'city', 'region', 'country', 'ip_address', 'last_updated')
    search_fields = ('user__username', 'township', 'district', 'city', 'region', 'country', 'ip_address')


@admin.register(UserChatProfile)
class UserChatProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'friend_id', 'avatar_label', 'avatar_image', 'color_theme', 'bubble_style', 'show_location')
    list_filter = ('color_theme', 'bubble_style', 'show_location')
    search_fields = ('user__username', 'avatar_label', 'friend_id')


@admin.register(UserSession)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'session_key', 'created_at')
    search_fields = ('user__username', 'session_key')

@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ('sender', 'recipient', 'status', 'created_at', 'responded_at')
    list_filter = ('status',)
    search_fields = ('sender__username', 'recipient__username')


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ('user', 'friend', 'created_at')
    search_fields = ('user__username', 'friend__username')


@admin.register(DirectConversation)
class DirectConversationAdmin(admin.ModelAdmin):
    list_display = ('user1', 'user2', 'created_at')
    search_fields = ('user1__username', 'user2__username')


@admin.register(DirectConversationState)
class DirectConversationStateAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'user', 'cleared_at')
    search_fields = ('conversation__user1__username', 'conversation__user2__username', 'user__username')


@admin.register(DirectMessage)
class DirectMessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'sender', 'created_at')
    search_fields = ('conversation__user1__username', 'conversation__user2__username', 'sender__username', 'content')
