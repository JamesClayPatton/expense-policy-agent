-- Synthetic seed data for the Northwind Labs expense agent.
-- All people, vendors and amounts are fictional.
--
-- Run after schema.sql. This RESETS employees, expenses and decisions, so it is
-- safe to run again but wipes the audit log. The policy chunks are loaded
-- separately by the ingest step.
--
-- "Today" for the whole project is 2026-10-22. The agent's tools take that date
-- from config instead of now(), so results do not change over time.
--
-- Spending counts toward a monthly budget when status is pending, approved or
-- paid (policy section 8.1). Rejected claims do not count.
--
-- Situations planted in this data, for writing test cases:
--   E012  meals, October: $270.00 of $300 used, $30.00 left
--   E015  travel, October: $1,850.00 of $2,000 used, $150.00 left
--   E017  software, October: $480.00 of $500 used, $20.00 left
--   E013  training, October: $200.00 of $250 used, $50.00 left
--   E018  office_supplies, October: $190.00 of $200 used, $10.00 left
--   E009  client_entertainment, October: $900.00 of $1,000 used, $100.00 left
--   E010  team_events, October: $280.00 of $300 used, $20.00 left
--   E014  Blue Door Bistro $58.40 on Oct 14 (a repeat submission is a duplicate)
--   E016  two $140.00 SkyPark parking claims 2 days apart (already a flag pair)
--   E016  Hotel Lumen $220.00 was rejected on Oct 6 (resubmission window, 2.3)
--   E013  one $400.00 Dell Direct claim, pending (a second $400 makes a split, 2.2)
--   E007  $612.00 Delta claim pending with no approver (a Manager cannot approve over $500)
--   E020  inactive employee (policy 1.1)
--   E016 reports to a Manager (E009), who can approve only up to $500

begin;

truncate decisions, expenses, employees restart identity cascade;

-- Insert order matters because manager_id points at another employee.
-- VPs
insert into employees (employee_id, name, role, department, manager_id, active) values
    ('E001', 'Priya Raman',    'vp',       'Engineering',  null,   true),
    ('E002', 'Marcus Webb',    'vp',       'Sales',        null,   true);

-- Directors
insert into employees (employee_id, name, role, department, manager_id, active) values
    ('E003', 'Elena Soto',     'director', 'Engineering',  'E001', true),
    ('E004', 'Tom Okafor',     'director', 'Sales',        'E002', true),
    ('E005', 'Hannah Berg',    'director', 'Marketing',    'E002', true),
    ('E006', 'Raj Patel',      'director', 'Finance',      'E001', true);

-- Managers
insert into employees (employee_id, name, role, department, manager_id, active) values
    ('E007', 'Lucy Chen',      'manager',  'Engineering',  'E003', true),
    ('E008', 'Dale Murphy',    'manager',  'Engineering',  'E003', true),
    ('E009', 'Sofia Alvarez',  'manager',  'Sales',        'E004', true),
    ('E010', 'Ben Carter',     'manager',  'Marketing',    'E005', true),
    ('E011', 'Grace Kim',      'manager',  'Finance',      'E006', true);

-- Employees
insert into employees (employee_id, name, role, department, manager_id, active) values
    ('E012', 'Omar Haddad',    'employee', 'Engineering',  'E007', true),
    ('E013', 'Jenna Liu',      'employee', 'Engineering',  'E007', true),
    ('E014', 'Victor Novak',   'employee', 'Engineering',  'E008', true),
    ('E015', 'Aisha Bello',    'employee', 'Sales',        'E009', true),
    ('E016', 'Colin Park',     'employee', 'Sales',        'E009', true),
    ('E017', 'Mia Rossi',      'employee', 'Marketing',    'E010', true),
    ('E018', 'Noah Fischer',   'employee', 'Marketing',    'E010', true),
    ('E019', 'Tara Singh',     'employee', 'Finance',      'E011', true),
    ('E020', 'Leo Duarte',     'employee', 'Engineering',  'E008', false);

-- Expense history. Columns:
-- id, employee, transaction date, submitted date, vendor, amount, category, status,
-- approver, business purpose, attendees, external attendee, itemized receipt
insert into expenses (
    expense_id, employee_id, transaction_date, submitted_date, vendor, amount,
    category, status, approver_id, business_purpose, attendee_count,
    has_external_attendee, receipt_itemized
) values
    -- E012 meals, October (total 270.00)
    ('X001', 'E012', '2026-10-02', '2026-10-03', 'Corner Deli',        32.10, 'meals', 'paid',     'E007', 'Lunch during sprint planning',          1, false, true),
    ('X002', 'E012', '2026-10-06', '2026-10-07', 'Blue Door Bistro',   58.40, 'meals', 'paid',     'E007', 'Dinner on release night',               1, false, true),
    ('X003', 'E012', '2026-10-09', '2026-10-09', 'Sunrise Cafe',       24.75, 'meals', 'paid',     'E007', 'Breakfast before customer demo',        1, false, true),
    ('X004', 'E012', '2026-10-13', '2026-10-14', 'Harbor Grill',       59.80, 'meals', 'approved', 'E007', 'Dinner during on-site work',            1, false, true),
    ('X005', 'E012', '2026-10-16', '2026-10-16', 'Corner Deli',        34.95, 'meals', 'approved', 'E007', 'Lunch during incident review',          1, false, true),
    ('X006', 'E012', '2026-10-20', '2026-10-21', 'Blue Door Bistro',   60.00, 'meals', 'pending',  null,   'Dinner during release week',            1, false, true),
    ('X031', 'E012', '2026-09-21', '2026-09-22', 'Harbor Grill',       55.00, 'meals', 'paid',     'E007', 'Dinner during on-site work',            1, false, true),

    -- E014: the Blue Door Bistro claim a repeat would duplicate
    ('X007', 'E014', '2026-10-14', '2026-10-15', 'Blue Door Bistro',   58.40, 'meals', 'approved', 'E008', 'Dinner after late deployment',          1, false, true),

    -- E015 travel, October (total 1850.00)
    ('X008', 'E015', '2026-10-01', '2026-10-01', 'Delta Air Lines',   780.00, 'travel', 'paid',     'E004', 'Round trip to Chicago client visit',    1, false, true),
    ('X009', 'E015', '2026-10-05', '2026-10-06', 'Marriott Chicago',  660.00, 'travel', 'paid',     'E004', 'Three nights, Chicago client visit',    1, false, true),
    ('X010', 'E015', '2026-10-12', '2026-10-12', 'Delta Air Lines',   410.00, 'travel', 'approved', 'E009', 'Flight to Denver prospect meeting',     1, false, true),
    ('X030', 'E015', '2026-09-10', '2026-09-11', 'Hilton Denver',     210.00, 'travel', 'paid',     'E009', 'One night, Denver trade show',          1, false, true),

    -- E016: parking pair, rejected hotel, and a September ride
    ('X011', 'E016', '2026-10-08', '2026-10-09', 'SkyPark Airport Parking', 140.00, 'travel', 'paid',    'E009', 'Airport parking, four days',      1, false, true),
    ('X012', 'E016', '2026-10-10', '2026-10-10', 'SkyPark Airport Parking', 140.00, 'travel', 'pending', null,   'Airport parking, four days',      1, false, true),
    ('X013', 'E016', '2026-10-06', '2026-10-07', 'Hotel Lumen',       220.00, 'travel', 'rejected', 'E009', 'Hotel for regional sales meeting',      1, false, false),
    ('X027', 'E016', '2026-09-22', '2026-09-23', 'Lyft',               38.20, 'travel', 'paid',     'E009', 'Ride to customer site',                1, false, true),

    -- E017 software, October (total 480.00)
    ('X014', 'E017', '2026-10-03', '2026-10-03', 'Adobe',              89.99, 'software', 'paid',     'E010', 'Creative Cloud for campaign assets', 1, false, true),
    ('X015', 'E017', '2026-10-03', '2026-10-04', 'Canva',             120.00, 'software', 'paid',     'E010', 'Canva Teams for social content',     1, false, true),
    ('X016', 'E017', '2026-10-10', '2026-10-10', 'Semrush',           270.01, 'software', 'approved', 'E005', 'SEO research tool, one seat',        1, false, true),
    ('X029', 'E017', '2026-09-15', '2026-09-16', 'Canva',             120.00, 'software', 'paid',     'E010', 'Canva Teams for social content',     1, false, true),

    -- E013: training (200.00) and a pending hardware claim (400.00)
    ('X018', 'E013', '2026-10-07', '2026-10-07', 'Pluralsight',       200.00, 'training', 'approved', 'E007', 'Cloud architecture course',          1, false, true),
    ('X023', 'E013', '2026-10-19', '2026-10-19', 'Dell Direct',       400.00, 'hardware', 'pending',  null,   'Second monitor for home office',     1, false, true),

    -- E018 office supplies, October (total 190.00) and a coffee
    ('X019', 'E018', '2026-10-04', '2026-10-04', 'Staples',           112.40, 'office_supplies', 'paid',     'E010', 'Printer paper and toner',      1, false, true),
    ('X020', 'E018', '2026-10-15', '2026-10-15', 'Office Depot',       77.60, 'office_supplies', 'approved', 'E010', 'Whiteboards and markers',      1, false, true),
    ('X040', 'E018', '2026-10-02', '2026-10-02', 'Starbucks',          18.60, 'meals',           'paid',     'E010', 'Coffee during agency meeting', 2, true,  false),

    -- E009 client entertainment, October (total 900.00)
    ('X021', 'E009', '2026-10-06', '2026-10-07', 'Capital Grille',    540.00, 'client_entertainment', 'paid',    'E004', 'Dinner with Hartwell Logistics, renewal',   4, true, true),
    ('X022', 'E009', '2026-10-14', '2026-10-14', 'Topgolf',           360.00, 'client_entertainment', 'pending', 'E004', 'Outing with Brightline Health prospects',   6, true, true),

    -- E010 team event, October (280.00)
    ('X026', 'E010', '2026-10-09', '2026-10-09', 'Bowlero',           280.00, 'team_events', 'approved', 'E005', 'Marketing team quarterly event',  4, false, true),

    -- E014 hardware
    ('X025', 'E014', '2026-10-08', '2026-10-08', 'Amazon Business',   215.50, 'hardware', 'approved', 'E008', 'Mechanical keyboard',                1, false, true),

    -- E012 hardware last month (does not count toward October)
    ('X024', 'E012', '2026-09-28', '2026-09-29', 'Logitech',          129.00, 'hardware', 'paid',     'E007', 'Webcam for remote meetings',         1, false, true),

    -- Senior staff
    ('X032', 'E003', '2026-10-07', '2026-10-08', 'Hotel Monaco',      640.00, 'travel', 'approved', 'E001', 'Two nights at engineering offsite',      1, false, true),
    ('X033', 'E003', '2026-10-15', '2026-10-15', 'United Airlines',   880.00, 'travel', 'pending',  null,   'Flight to engineering offsite',         1, false, true),
    ('X034', 'E001', '2026-10-12', '2026-10-13', 'Nobu',              780.00, 'client_entertainment', 'paid', 'E002', 'Dinner with Ridgeway Systems executives', 5, true, true),
    ('X035', 'E002', '2026-10-01', '2026-10-01', 'Zoom',              149.90, 'software', 'paid',    'E001',   'Zoom Workplace annual seat',           1, false, true),
    ('X037', 'E007', '2026-10-12', '2026-10-13', 'Delta Air Lines',   612.00, 'travel', 'pending',  null,   'Flight to partner summit',              1, false, true),
    ('X038', 'E008', '2026-10-17', '2026-10-17', 'Chipotle',           87.30, 'meals', 'approved', 'E003', 'Team lunch during planning day',         4, false, true),

    -- Finance
    ('X028', 'E019', '2026-09-30', '2026-10-01', 'Corner Deli',        29.99, 'meals', 'paid',     'E011', 'Lunch during quarter-end close',        1, false, true),
    ('X017', 'E019', '2026-10-05', '2026-10-05', 'Figma',              45.00, 'software', 'paid',  'E011', 'Design review seat',                    1, false, true),
    ('X039', 'E019', '2026-10-12', '2026-10-13', 'Whole Foods',        34.10, 'meals', 'paid',     'E011', 'Lunch during audit week',               1, false, true),
    ('X036', 'E011', '2026-10-10', '2026-10-10', 'Staples',            64.25, 'office_supplies', 'paid', 'E006', 'Binders and labels for audit',    1, false, true);

commit;
