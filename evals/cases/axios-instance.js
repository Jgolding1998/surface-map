import axios from 'axios';
const client = axios.create({baseURL: '/api'});
client.post('/items', {});
