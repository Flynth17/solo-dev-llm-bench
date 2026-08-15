/** Solo Dev LLM Bench - Results page utility helpers (extracted). */

function fmt2(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseFloat(value);
    if (isNaN(num)) return "\u2014";
    return num.toFixed(2);
}

function fmtInt(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseInt(value, 10);
    if (isNaN(num)) return "\u2014";
    return num.toString();
}

function formatTtft(value) {
    if (value === null || value === undefined || value === "") return "\u2014";
    var num = parseFloat(value);
    if (isNaN(num)) return "\u2014";
    if (num >= 1) {
        return num.toFixed(2) + " s";
    }
    var ms = Math.round(num * 1000);
    return ms + " ms";
}

function formatTimestamp(iso) {
    if (!iso) return "";
    try {
        var normalized = iso;
        if (/[+-]\d{2}:\d{2}$/.test(normalized)) {
            normalized = normalized.replace(/[+-]\d{2}:\d{2}$/, "Z");
        } else if (!normalized.endsWith("Z")) {
            normalized = normalized + "Z";
        }
        var d = new Date(normalized);
        if (isNaN(d.getTime())) return iso;
        var day = d.getDate();
        var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
        var month = months[d.getMonth()];
        var year = d.getFullYear();
        var hh = d.getHours().toString().padStart(2, "0");
        var mm = d.getMinutes().toString().padStart(2, "0");
        return day + " " + month + " " + year + ", " + hh + ":" + mm;
    } catch (e) {
        return iso;
    }
}

function escapeHtml(str) {
    if (!str) return "";
    var AMP = String.fromCharCode(38) + "amp" + String.fromCharCode(59);
    var LT = String.fromCharCode(60) + "lt" + String.fromCharCode(59);
    var GT = String.fromCharCode(62) + "gt" + String.fromCharCode(59);
    var QUOT = String.fromCharCode(34) + "quot" + String.fromCharCode(59);
    var APOS = String.fromCharCode(39) + "39" + String.fromCharCode(59);
    var result = "";
    for (var i = 0; i < str.length; i++) {
        var ch = str.charAt(i);
        if (ch === "&") { result += AMP; }
        else if (ch === "<") { result += LT; }
        else if (ch === ">") { result += GT; }
        else if (ch === '"') { result += QUOT; }
        else if (ch === "'") { result += APOS; }
        else { result += ch; }
    }
    return result;
}