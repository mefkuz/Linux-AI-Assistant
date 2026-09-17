"""
Bilinen CLI tabanlı AI araçlarının merkezi kayıt defteri.
Her araç için:
  - binary:       Terminalde çalıştırılan komut adı
  - input_method: "stdin" (pipe) | "arg" (doğrudan argüman)
  - model_flag:   Model belirtmek için kullanılan flag ("-m", "--model" vb.)
                  "run" ise özel ollama formatı: `ollama run <model>`
  - models:       Önerilen model listesi (boş = model seçimi yok)
  - description:  Kullanıcıya gösterilecek açıklama
"""

KNOWN_CLI_TOOLS = {
    "agy (Antigravity)": {
        "binary": "agy",
        "input_method": "stdin",
        "model_flag": None,
        "models": [],
        "extra_args": ["--dangerously-skip-permissions"], # Headless modda komut onaylarını atlamak için
        "description": "Antigravity — Google DeepMind AI asistanı",
    }
}

# Komut satırını oluşturmak için yardımcı fonksiyon
def build_cli_command(tool_key, model=None):
    """
    Kayıt defterinden verilen araca uygun komut listesi oluşturur.
    Döndürdüğü liste subprocess.run(cmd, input=prompt) ile kullanılır.
    """
    cfg = KNOWN_CLI_TOOLS.get(tool_key)
    if not cfg:
        # Bilinmeyen araç — binary'i doğrudan kullan
        return [tool_key]

    binary       = cfg["binary"]
    input_method = cfg["input_method"]
    model_flag   = cfg["model_flag"]

    if model_flag == "run":
        # Ollama özel formatı: ollama run <model>
        cmd = [binary, "run", model] if model else [binary, "run"]
    elif model_flag and model:
        # Standart: tool -m <model>
        cmd = [binary, model_flag, model]
    else:
        cmd = [binary]

    # Varsa aracın varsayılan ek parametrelerini (ör: --dangerously-skip-permissions) ekle
    if "extra_args" in cfg:
        cmd.extend(cfg["extra_args"])

    return cmd
