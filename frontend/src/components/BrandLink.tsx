import { Link } from 'react-router-dom';

export default function BrandLink() {
  return (
    <Link to="/cases" className="brand brand-link">
      <span className="brand-text">
        GLDB <span>Fraud Review</span>
      </span>
    </Link>
  );
}
