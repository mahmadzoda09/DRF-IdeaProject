from django.db import migrations

DEFAULT_NAME = "Саховат"


def forwards(apps, schema_editor):
    """У каждой аптеки — клиент «Саховат» с её адресом; все прежние продажи записываются на него."""
    Pharmacy = apps.get_model("pharmacies", "Pharmacy")
    Client = apps.get_model("pharmacies", "Client")
    Order = apps.get_model("orders", "Order")
    for pharmacy in Pharmacy.objects.all():
        client, _ = Client.objects.get_or_create(
            pharmacy=pharmacy, name=DEFAULT_NAME, defaults={"address": pharmacy.address}
        )
        if not client.is_default:
            client.is_default = True
            client.save(update_fields=["is_default"])
        Order.objects.filter(pharmacy=pharmacy, client__isnull=True).update(client=client)


def backwards(apps, schema_editor):
    apps.get_model("orders", "Order").objects.update(client=None)


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0002_order_client"),
        ("pharmacies", "0003_client"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
