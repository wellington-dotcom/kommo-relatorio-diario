name: Traduzir Nomes Antigos (manual, rodar uma vez)

on:
  workflow_dispatch:

jobs:
  rodar:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Instalar dependencias
        run: pip install -r requirements.txt

      - name: Traduzir nomes antigos na planilha
        env:
          KOMMO_TOKEN: ${{ secrets.KOMMO_TOKEN }}
          SHEET_ID: ${{ secrets.SHEET_ID }}
          GOOGLE_CREDENTIALS: ${{ secrets.GOOGLE_CREDENTIALS }}
        run: python traduzir_nomes_antigos.py
