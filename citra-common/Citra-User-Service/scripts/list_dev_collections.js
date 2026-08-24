// Copyright (c) 2026 Trustedwear Tech Private Limited (https://citra-ai.com)
// Author: Rohit Kumar Chandan
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0

/**
 * Read-only: list every collection in the dev DB with its document
 * count. Lets us pick which to wipe vs keep.
 */
require('dotenv').config();
const dns = require('dns');
dns.setServers(['8.8.8.8', '1.1.1.1']);
const mongoose = require('mongoose');

async function main() {
  await mongoose.connect(process.env.MONGODB_CONNECTION_STRING, {
    dbName: process.env.MONGODB_DATABASE || 'dev',
  });
  const db = mongoose.connection.db;
  const cols = await db.listCollections().toArray();
  console.log(`db = ${db.databaseName}, collections = ${cols.length}\n`);
  const rows = [];
  for (const c of cols) {
    const count = await db.collection(c.name).countDocuments().catch(() => -1);
    rows.push({ name: c.name, count });
  }
  rows.sort((a, b) => a.name.localeCompare(b.name));
  for (const r of rows) console.log(`  ${r.name.padEnd(38)} ${r.count}`);
  await mongoose.disconnect();
}
main().catch(e => { console.error(e); process.exit(1); });
