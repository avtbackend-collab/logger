const express = require('express');
const app = express();
app.use(express.json());

// Simple in-memory storage for logs
let logs = [];

// Endpoint to receive logs from Victim (GRIND)
app.post('/log', (req, res) => {
    const entry = {
        id: Date.now(),
        timestamp: new Date().toISOString(),
        data: req.body
    };
    logs.push(entry);
    
    // Log to Render console for debugging
    console.log(`[LOG] Received: ${JSON.stringify(req.body)}`);
    
    res.json({ status: 'success', id: entry.id });
});

// Endpoint to view logs
app.get('/logs', (req, res) => {
    // Return last 50 logs
    res.json(logs.slice(-50));
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Grind Logger running on port ${PORT}`);
});
