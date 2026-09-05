const isTr = navigator.language.startsWith('tr');

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
        id: "send-to-linux-ai",
        title: isTr ? "Linux AI Asistan'a Sor" : "Ask Linux AI Assistant",
        contexts: ["page", "selection"]
    });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "send-to-linux-ai") {
        sendContextToAI(tab, info.selectionText);
    }
});

function sendContextToAI(tab, fallbackSelection) {
    const sendData = (data) => {
        fetch("http://127.0.0.1:8765", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        }).catch(err => console.error("Error sending to AI:", err));
    };

    chrome.scripting.executeScript({
        target: {tabId: tab.id},
        files: ['content.js']
    }, () => {
        if (chrome.runtime.lastError) {
            console.log("Injection failed, using fallback:", chrome.runtime.lastError);
            sendData({
                title: tab.title || "",
                url: tab.url || "",
                selection: fallbackSelection || "",
                content: ""
            });
            return;
        }

        chrome.tabs.sendMessage(tab.id, {action: "getContext"}, (response) => {
            let data = response || {};
            if (chrome.runtime.lastError) {
                console.log("SendMessage failed, using fallback:", chrome.runtime.lastError);
                data = {
                    title: tab.title || "",
                    url: tab.url || "",
                    selection: fallbackSelection || "",
                    content: ""
                };
            } else if (fallbackSelection && !data.selection) {
                data.selection = fallbackSelection;
            }
            sendData(data);
        });
    });
}

// SSE Connection for Two-Way Communication
let sse = null;
function connectSSE() {
    if (sse) sse.close();
    sse = new EventSource("http://127.0.0.1:8765/events");
    
    sse.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            if (data.action === "ping") return;
            
            chrome.tabs.query({active: true, currentWindow: true}, function(tabs) {
                if (!tabs || tabs.length === 0) return;
                let tab = tabs[0];
                
                if (data.action === "close_tab") {
                    chrome.tabs.remove(tab.id);
                } else if (data.action === "new_tab") {
                    chrome.tabs.create({url: data.params?.url || "https://google.com"});
                } else if (data.action === "scroll_down") {
                    chrome.scripting.executeScript({
                        target: {tabId: tab.id},
                        func: () => window.scrollBy({top: window.innerHeight * 0.8, behavior: 'smooth'})
                    });
                } else if (data.action === "scroll_up") {
                    chrome.scripting.executeScript({
                        target: {tabId: tab.id},
                        func: () => window.scrollBy({top: -window.innerHeight * 0.8, behavior: 'smooth'})
                    });
                } else if (data.action === "fill_form") {
                    chrome.scripting.executeScript({
                        target: {tabId: tab.id},
                        func: (text) => {
                            let activeEl = document.activeElement;
                            if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) {
                                activeEl.value = text;
                                activeEl.dispatchEvent(new Event('input', { bubbles: true }));
                                activeEl.dispatchEvent(new Event('change', { bubbles: true }));
                            } else if (activeEl && activeEl.isContentEditable) {
                                activeEl.innerText = text;
                                activeEl.dispatchEvent(new Event('input', { bubbles: true }));
                            } else {
                                let inputs = document.querySelectorAll('input[type="text"], textarea, [contenteditable="true"]');
                                for(let inp of inputs) {
                                    if(inp.isContentEditable) {
                                        if(!inp.innerText.trim()) {
                                            inp.innerText = text;
                                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                                            break;
                                        }
                                    } else {
                                        if(!inp.value) {
                                            inp.value = text;
                                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                                            break;
                                        }
                                    }
                                }
                            }
                        },
                        args: [data.params?.text || ""]
                    });
                }
            });
        } catch (e) {}
    };

    sse.onerror = function() {
        sse.close();
        setTimeout(connectSSE, 3000);
    };
}
connectSSE();
