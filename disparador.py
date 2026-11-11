import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORNECEDORES_FILE = os.path.join(BASE_DIR, "fornecedores.json")
HISTORICO_FILE = os.path.join(BASE_DIR, "historico_envios.json")
RELATORIO_FILE = os.path.join(BASE_DIR, "relatorio_disparo.txt")

BRIDGE_URL = "http://localhost:3050"
NODE_EXE = r"C:\Users\Mundo Apple\.gemini\antigravity\scratch\node-env\node-v20.17.0-win-x64\node.exe"
BRIDGE_SCRIPT = os.path.join(BASE_DIR, "whatsapp_bridge.js")

MENSAGEM_PADRAO = (
    "Bom dia, tudo bem? Somos loja física aqui na Santa Efigênia. "
    "Gostaria de entrar na sua lista de transmissão."
)

# Intervalo seguro entre cada envio (segundos) para proteger o chip contra bloqueio
INTERVALO_MIN_SEG = 7
INTERVALO_MAX_SEG = 13


def get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def garantir_servidor_bridge():
    try:
        get_json(f"{BRIDGE_URL}/api/status")
        return
    except Exception:
        print("[*] Iniciando motor de conexão WhatsApp (whatsapp_bridge.js)...")
        subprocess.Popen(
            [NODE_EXE, BRIDGE_SCRIPT],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            time.sleep(1)
            try:
                get_json(f"{BRIDGE_URL}/api/status")
                print("[+] Motor WhatsApp iniciado em http://localhost:3050")
                return
            except Exception:
                pass


def carregar_fornecedores():
    with open(FORNECEDORES_FILE, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def carregar_historico():
    if os.path.exists(HISTORICO_FILE):
        try:
            with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_historico(hist):
    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def registrar_relatorio(linha):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    texto = f"[{ts}] {linha}"
    print(texto, flush=True)
    with open(RELATORIO_FILE, "a", encoding="utf-8") as f:
        f.write(texto + "\n")


def main():
    print("=" * 65)
    print("  DISPARADOR AUTOMÁTICO WHATSAPP - 204 FORNECEDORES PXT (PYTHON)")
    print("=" * 65)
    print(f"Mensagem configurada:\n  \"{MENSAGEM_PADRAO}\"\n")

    garantir_servidor_bridge()
    fornecedores = carregar_fornecedores()
    historico = carregar_historico()

    pendentes = [
        f for f in fornecedores
        if str(f.get("telefone", "")).strip() not in historico
        or historico[str(f.get("telefone", "")).strip()].get("status") != "ENVIADO"
    ]

    print(f"[*] Total na lista: {len(fornecedores)} fornecedores")
    print(f"[*] Já enviados anteriormente: {len(fornecedores) - len(pendentes)}")
    print(f"[*] Pendentes para envio: {len(pendentes)}\n")

    print("[*] Aguardando conexão do seu WhatsApp...")
    print("    -> Escaneie o QR Code na tela ou em http://localhost:3050\n")

    while True:
        try:
            st = get_json(f"{BRIDGE_URL}/api/status")
            status = st.get("status")
            if status == "CONNECTED":
                user = st.get("user") or {}
                registrar_relatorio(
                    f"WHATSAPP CONECTADO: {user.get('name')} ({user.get('id')})! Iniciando disparos..."
                )
                break
        except Exception:
            pass
        time.sleep(2)

    # Pausa curta de estabilização após conectar
    time.sleep(3)

    enviados_sessao = 0
    erros_sessao = 0
    total = len(pendentes)

    for idx, item in enumerate(pendentes, start=1):
        nome = item.get("nome", "Fornecedor")
        telefone = str(item.get("telefone", "")).strip()

        # Verificar se continua conectado antes de cada envio
        while True:
            try:
                st = get_json(f"{BRIDGE_URL}/api/status")
                if st.get("status") == "CONNECTED":
                    break
                print("[!] Conexão oscilou, aguardando reconexão automática...", flush=True)
            except Exception:
                pass
            time.sleep(3)

        try:
            resp = post_json(
                f"{BRIDGE_URL}/api/send",
                {"phone": telefone, "name": nome, "message": MENSAGEM_PADRAO},
            )
            if resp.get("ok"):
                enviados_sessao += 1
                historico[telefone] = {
                    "nome": nome,
                    "telefone": telefone,
                    "jid": resp.get("jid"),
                    "status": "ENVIADO",
                    "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                salvar_historico(historico)
                registrar_relatorio(f"({idx}/{total}) OK -> {nome} ({telefone})")
            else:
                erros_sessao += 1
                historico[telefone] = {
                    "nome": nome,
                    "telefone": telefone,
                    "status": "ERRO",
                    "erro": resp.get("error"),
                    "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                salvar_historico(historico)
                registrar_relatorio(f"({idx}/{total}) ERRO -> {nome} ({telefone}): {resp.get('error')}")
        except urllib.error.HTTPError as he:
            erros_sessao += 1
            err_body = he.read().decode("utf-8", errors="ignore")
            historico[telefone] = {
                "nome": nome,
                "telefone": telefone,
                "status": "ERRO",
                "erro": err_body,
                "horario": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            salvar_historico(historico)
            registrar_relatorio(f"({idx}/{total}) FALHA HTTP -> {nome} ({telefone}): {err_body}")
        except Exception as e:
            erros_sessao += 1
            registrar_relatorio(f"({idx}/{total}) EXCEÇÃO -> {nome} ({telefone}): {e}")

        if idx < total:
            pausa = round(random.uniform(INTERVALO_MIN_SEG, INTERVALO_MAX_SEG), 1)
            time.sleep(pausa)

    registrar_relatorio(
        f"FINALIZADO! Enviados com sucesso: {enviados_sessao} | Falhas: {erros_sessao}"
    )


if __name__ == "__main__":
    main()
