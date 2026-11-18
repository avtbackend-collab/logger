const express = require('express');
const app = express();

// Middleware to log all requests
app.use((req, res, next) => {
    console.log(`${req.method} ${req.url}`);
    next();
});

// Simple health check for Uptime Robot
app.get('/ping', (req, res) => {
    res.send('OK');
});

// Your other routes...
app.post('/log', (req, res) => {
    // ... existing code ...
    res.json({ status: 'success' });
});

app.get('/logs', (req, res) => {
    // ... existing code ...
    res.json([]);
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
});
