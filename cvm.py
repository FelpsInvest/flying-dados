name: Fundamentos da CVM e FIIs

on:
  workflow_dispatch:
  schedule:
    - cron: "17 9 * * 1"   # toda segunda, 06:17 em Brasília

permissions:
  contents: write

jobs:
  atualizar:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Baixar e calcular os fundamentos
        run: python cvm.py
      - name: Baixar e calcular os FIIs
        run: python fii.py
        continue-on-error: true
        timeout-minutes: 30
      - name: Salvar o resultado no repositório
        run: |
          git config user.name "robo-cvm"
          git config user.email "robo-cvm@users.noreply.github.com"
          git add dados/fundamentos.json
          git add dados/fiis.json || true
          git diff --cached --quiet || git commit -m "Atualiza fundamentos da CVM e FIIs"
          git push
