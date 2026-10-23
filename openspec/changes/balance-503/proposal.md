# Restore balance exhaustion outcomes

Synthetic status 503 from account pause rules is being classified as a transient upstream failure. Clear the prior upstream status after a rule consumes the error so pool selection and exhaustion retain their error code and reset eligibility. Genuine transient upstream failures continue to fail over.
