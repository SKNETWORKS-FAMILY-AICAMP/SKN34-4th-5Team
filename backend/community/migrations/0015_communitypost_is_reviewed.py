from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("community", "0014_alter_moderationsettings_mode")]

    operations = [
        migrations.AddField(
            model_name="communitypost",
            name="is_reviewed",
            field=models.BooleanField(default=False),
        ),
    ]
