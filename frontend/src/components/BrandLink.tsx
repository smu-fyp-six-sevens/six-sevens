import { Link } from 'react-router-dom';

export default function BrandLink() {
  return (
    <Link to="/cases" className="brand brand-link">
      GLDB <span>Fraud Review</span>
    </Link>
  );
}
