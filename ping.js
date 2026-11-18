const express = require('express');
const app = express();

// Simple endpoint for Uptime Robot
app.get('/ping', (req, res) => {
    res.send('OK');
});

module.exports = app;
