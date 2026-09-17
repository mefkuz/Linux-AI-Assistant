const isTr = navigator.language.startsWith('tr');
const t = {
    email: isTr ? "E-postayı Okut" : "Read Email",
    pdf: isTr ? "PDF'i Okut" : "Read PDF",
    video: isTr ? "Videoyu Okut" : "Read Video",
    wiki: isTr ? "Makaleyi Okut" : "Read Article",
    page: isTr ? "Sayfayı Okut" : "Read Page",
    fetching: isTr ? "Veri alınıyor..." : "Fetching data...",
    errorRead: isTr ? "Hata: Sayfa okunamadı." : "Error: Cannot read page.",
    sending: isTr ? "Asistana gönderiliyor..." : "Sending to AI...",
    success: isTr ? "✓ Asistana aktarıldı!" : "✓ Sent to Assistant!",
    errorNoResp: isTr ? "Hata: Asistan yanıt vermedi." : "Error: No response.",
    errorApp: isTr ? "Hata: Asistan açık mı?" : "Error: Is Assistant running?"
};

chrome.tabs.query({active: true, currentWindow: true}).then(([tab]) => {
    let btn = document.getElementById('sendBtn');
    if (!tab) return;
    
    if (tab.url.includes("mail.google.com")) {
        btn.innerText = t.email;
    } else if (tab.url.toLowerCase().endsWith(".pdf")) {
        btn.innerText = t.pdf;
    } else if (tab.url.includes("youtube.com/watch") || tab.url.includes("youtu.be/")) {
        btn.innerText = t.video;
    } else if (tab.url.includes("wikipedia.org")) {
        btn.innerText = t.wiki;
    } else {
        btn.innerText = t.page;
    }
});

document.getElementById('sendBtn').addEventListener('click', async () => {
    let [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    document.getElementById('status').innerText = t.fetching;
    
    chrome.scripting.executeScript({
        target: {tabId: tab.id},
        files: ['content.js']
    }, () => {
        chrome.tabs.sendMessage(tab.id, {action: "getContext"}, (response) => {
            if (chrome.runtime.lastError || !response) {
                document.getElementById('status').innerText = t.errorRead;
                return;
            }
            
            document.getElementById('status').innerText = t.sending;
            
            fetch("http://127.0.0.1:8765", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(response)
            }).then(res => {
                if (res.ok) {
                    document.getElementById('status').innerText = t.success;
                    setTimeout(() => window.close(), 1500);
                } else {
                    document.getElementById('status').innerText = t.errorNoResp;
                }
            }).catch(err => {
                document.getElementById('status').innerText = t.errorApp;
            });
        });
    });
});
