from django.test import SimpleTestCase

from .site_config import validate_bank_transfer_configuration


class BankTransferConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.valid = {
            "BANK_TRANSFER_ACCOUNT_HOLDER": "SC Viajes Operaciones",
            "BANK_TRANSFER_ALIAS": "scviajes.cobros",
            "BANK_TRANSFER_CVU": "0001234567890123456789",
            "BANK_TRANSFER_CUIT": "20-12345678-6",
            "BANK_TRANSFER_ENTITY": "Entidad bancaria",
        }

    def test_valid_configuration_is_accepted(self):
        self.assertEqual(validate_bank_transfer_configuration(self.valid), [])

    def test_missing_configuration_is_rejected(self):
        self.assertTrue(validate_bank_transfer_configuration({name: "" for name in self.valid}))

    def test_placeholders_and_obvious_sequences_are_rejected(self):
        placeholder = dict(self.valid, BANK_TRANSFER_ACCOUNT_HOLDER="Empresa Ejemplo", BANK_TRANSFER_CVU="0" * 22)
        self.assertIn("BANK_TRANSFER_ACCOUNT_HOLDER", validate_bank_transfer_configuration(placeholder))
        self.assertIn("BANK_TRANSFER_CVU", validate_bank_transfer_configuration(placeholder))

    def test_invalid_formats_are_rejected_without_echoing_values(self):
        invalid = dict(self.valid, BANK_TRANSFER_ALIAS="alias inválido", BANK_TRANSFER_CUIT="30-12345678-9")
        errors = validate_bank_transfer_configuration(invalid)
        self.assertIn("BANK_TRANSFER_ALIAS_FORMAT", errors)
        self.assertIn("BANK_TRANSFER_CUIT_FORMAT", errors)
