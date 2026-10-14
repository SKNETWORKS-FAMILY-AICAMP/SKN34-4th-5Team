from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('community', '0015_communitypost_is_reviewed')]

    operations = [
        migrations.RemoveConstraint(model_name='moderationjob', name='community_mod_post_revision'),
        migrations.AddConstraint(
            model_name='moderationjob',
            constraint=models.UniqueConstraint(
                fields=('post', 'revision'), name='community_mod_post_revision',
                condition=models.Q(payload__trigger__isnull=True) | ~models.Q(payload__trigger='report'),
            ),
        ),
    ]
