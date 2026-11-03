from django.db import migrations


def default_name(pharmacy):
    return f"Клиент {(pharmacy.address or pharmacy.name).strip()}"[:255]


def forwards(apps, schema_editor):
    """Клиент по умолчанию называется по адресу аптеки: «Саховат» -> «Клиент Саховат»; у аптек без него — создаётся."""
    Pharmacy = apps.get_model("pharmacies", "Pharmacy")
    Client = apps.get_model("pharmacies", "Client")
    for pharmacy in Pharmacy.objects.all():
        name = default_name(pharmacy)
        client = Client.objects.filter(pharmacy=pharmacy, is_default=True).first()
        if client is None:
            Client.objects.create(pharmacy=pharmacy, name=name, address=pharmacy.address, is_default=True)
        elif client.name != name and not Client.objects.filter(pharmacy=pharmacy, name=name).exists():
            client.name = name
            client.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [("orders", "0003_default_clients")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
