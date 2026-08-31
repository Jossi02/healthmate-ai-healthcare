const express = require('express');
const router = express.Router();

router.use((_req, res) => {
  res.status(404).json({ error: 'Not found.' });
});

module.exports = router;
