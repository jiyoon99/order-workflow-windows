const OrderApi = {
  json(path, options = {}) {
    return fetch(path, {
      ...options,
      headers: {
        ...(options.body && !(options.body instanceof FormData) ? {"Content-Type":"application/json"} : {}),
        ...(options.headers || {}),
      },
    });
  },
  get(path) {
    return fetch(path);
  },
  postJson(path, payload = {}) {
    return this.json(path, { method:"POST", body:JSON.stringify(payload) });
  },
  patchJson(path, payload = {}) {
    return this.json(path, { method:"PATCH", body:JSON.stringify(payload) });
  },
  postForm(path, formData) {
    return fetch(path, { method:"POST", body:formData });
  },
};
