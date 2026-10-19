import {defineConfig} from '@playwright/test';
export default defineConfig({testDir:'./tests/browser',workers:1,use:{baseURL:process.env.EVIDENCEMAP_TEST_URL||'http://127.0.0.1:8000',channel:'msedge',headless:true},reporter:'list'});
