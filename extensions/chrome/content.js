// Extract text content from the body, removing scripts and styles
function getCleanText() {
    // Gmail Özel Heuristic
    if (window.location.hostname === "mail.google.com") {
        let openEmails = document.querySelectorAll('.a3s.aiL');
        if (openEmails.length > 0) {
            let emailText = "";
            openEmails.forEach((el, index) => {
                emailText += `\n[Mail ${index + 1}]:\n` + el.innerText + "\n";
            });
            return "GMAIL İÇERİĞİ:\n" + emailText.trim();
        }
    }
    
    let clone = document.body.cloneNode(true);
    let elementsToRemove = clone.querySelectorAll('script, style, noscript, iframe, svg');
    elementsToRemove.forEach(el => el.remove());
    return clone.innerText.replace(/\n\s*\n/g, '\n').trim();
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "getContext") {
        sendResponse({
            title: document.title,
            url: window.location.href,
            selection: window.getSelection().toString(),
            content: getCleanText(),
            contentType: document.contentType
        });
    }
});
