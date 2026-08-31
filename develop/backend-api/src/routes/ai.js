const express = require('express');
const router = express.Router();

router.use((_req, res) => {
  res.status(410).json({ error: 'Legacy AI API is no longer available.' });
});

module.exports = router;
