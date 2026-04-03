from django.db import models


class TeamsUserIdentity(models.Model):
    conversation_id = models.CharField(max_length=255)
    channel_id = models.CharField(max_length=50, default="msteams")
    user_id = models.CharField(max_length=255)
    user_name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(max_length=254, blank=True, default="", db_index=True)
    tenant_id = models.CharField(max_length=255, blank=True, default="")
    aad_object_id = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "custom_teams_user_identity"
        constraints = [
            models.UniqueConstraint(
                fields=["conversation_id", "user_id"],
                name="custom_team_user_conv_unique",
            )
        ]

    def __str__(self):
        return f"{self.conversation_id}:{self.user_id}:{self.email}"
