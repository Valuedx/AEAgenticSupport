from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TeamsUserIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("conversation_id", models.CharField(max_length=255)),
                ("channel_id", models.CharField(default="msteams", max_length=50)),
                ("user_id", models.CharField(max_length=255)),
                ("user_name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "email",
                    models.EmailField(
                        blank=True, db_index=True, default="", max_length=254
                    ),
                ),
                ("tenant_id", models.CharField(blank=True, default="", max_length=255)),
                (
                    "aad_object_id",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "custom_teams_user_identity",
            },
        ),
        migrations.AddConstraint(
            model_name="teamsuseridentity",
            constraint=models.UniqueConstraint(
                fields=("conversation_id", "user_id"),
                name="custom_team_user_conv_unique",
            ),
        ),
    ]
