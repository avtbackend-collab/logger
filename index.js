const express = require('express');
const fs = require('fs');
const path = require('path');

const app = express();
// Default 100kb limit is easy to exceed with batched events
app.use(express.json({ limit: '1mb' }));

const DATA_FILE = path.join(__dirname, 'logs.jsonl');
const MAX_LOGS = 1000;

let logs = [];
let seq = 0;

// Reload anything this container already wrote, so a restart doesn't erase the history
try {
if (fs.existsSync(DATA_FILE)) {
logs = fs
.readFileSync(DATA_FILE, 'utf8')
.split('\n')
.filter(Boolean)
.map(function (line) { return JSON.parse(line); })
.slice(-MAX_LOGS);
console.log('Reloaded ' + logs.length + ' entries from disk');
}
} catch (err) {
console.error('reload failed: ' + err.message);
}

function store(body) {
seq = seq + 1;
const entry = {
id: String(Date.now()) + '-' + String(seq), // unique even within the same millisecond
timestamp: new Date().toISOString(),
data: body,
};
logs.push(entry);
if (logs.length > MAX_LOGS) logs.splice(0, logs.length - MAX_LOGS);
try {
fs.appendFileSync(DATA_FILE, JSON.stringify(entry) + '\n');
} catch (err) {
console.error('append failed: ' + err.message);
}
console.log('[LOG] Received: ' + JSON.stringify(body).slice(0, 300));
return entry;
}

app.get('/', function (req, res) { res.send('OK'); });
app.get('/ping', function (req, res) { res.send('OK'); });

app.post('/log', function (req, res) {
const body = req.body || {};

// Batched drain payload from the client
if (Array.isArray(body.events)) {
const ids = body.events.map(store);
return res.status(201).json({ status: 'success', count: ids.length });
}

const entry = store(body);
res.status(201).json({ status: 'success', id: entry.id });
});

app.get('/logs', function (req, res) { res.json(logs.slice(-50)); });

// Cheap incremental read for the dashboard
app.get('/logs/since/:ms', function (req, res) {
const since = Number(req.params.ms) || 0;
res.json(logs.filter(function (e) { return Date.parse(e.timestamp) > since; }).slice(-500));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, function () { console.log('Grind Logger running on port ' + PORT); });
